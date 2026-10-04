"""可追溯静态论文图片：公开来源下载、许可检查、数据重绘与资源核验。"""
import hashlib
import io
import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

HOSTS={'arxiv.org','export.arxiv.org','proceedings.mlr.press','openaccess.thecvf.com',
       'www.ecva.net','jmlr.org','aclanthology.org','openreview.net'}
LICENSES={'CC0','CC-BY','CC-BY-SA'}
MAX_DOWNLOAD=8*1024*1024
Image.MAX_IMAGE_PIXELS=24_000_000


def validate_descriptor(f):
    from .__main__ import https_url
    for key in ('source_url','figure_number','caption','alt','version'):
        if not isinstance(f.get(key),str) or not f[key].strip():raise ValueError('图片缺少 '+key)
    https_url(f['source_url'])
    if f.get('kind') not in {'original','redraw','link'}:raise ValueError('图片类型无效')
    if f['kind']=='original':
        if f.get('license') not in LICENSES:raise ValueError('原图转载许可未核实')
        for key in ('license_url','attribution','license_evidence','third_party_check'):
            if not f.get(key):raise ValueError('原图缺少 '+key)
        https_url(f['license_url'])
    if f['kind']=='redraw':
        if not f.get('data') or not f.get('data_evidence') or not f.get('conditions'):
            raise ValueError('重绘缺少数据、证据或实验条件')
        for claim in f['data_evidence']:
            if not claim.get('locator') or not claim.get('excerpt'):raise ValueError('重绘数据缺少定位')
            https_url(claim.get('url',''))
        for row in f['data']:
            if not isinstance(row.get('label'),str) or not isinstance(row.get('value'),(int,float)):
                raise ValueError('重绘数据无效')
            import math
            if not math.isfinite(row['value']):raise ValueError('重绘数值无效')
            from .quality import numeric_tokens
            value=next(iter(numeric_tokens(str(row['value']))))[0]
            if not any(value in {n for n,_ in numeric_tokens(e['excerpt'])} for e in f['data_evidence']):
                raise ValueError('重绘数值缺少对应原文证据')
    return f


def safe_url(url):
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in HOSTS or p.username or p.password or p.port not in {None,443}:
        raise ValueError('图片来源不在公开官方白名单')
    for _,_,_,_,address in socket.getaddrinfo(p.hostname,443,type=socket.SOCK_STREAM):
        ip=ipaddress.ip_address(address[0])
        # Some local DNS proxies map public hosts into RFC 2544's synthetic pool.
        # Only fixed official hosts are permitted; TLS hostname verification remains enabled.
        synthetic=ip.version==4 and ip in ipaddress.ip_network('198.18.0.0/15')
        if not ip.is_global and not synthetic:raise ValueError('拒绝非公网图片地址')
    return url


def download(url):
    from .adapters.public import SourceError
    from email.utils import parsedate_to_datetime
    from datetime import datetime,timezone
    import time
    for redirect in range(4):
        safe_url(url)
        for attempt in range(3):
            try:response=requests.get(url,stream=True,allow_redirects=False,timeout=(5,15))
            except requests.RequestException:
                if attempt==2:raise
                time.sleep(2**attempt);continue
            if response.status_code>=500 and attempt<2:
                response.close();time.sleep(2**attempt);continue
            if response.status_code==429:
                delay=response.headers.get('Retry-After','3');response.close()
                try:seconds=max(0,float(delay))
                except ValueError:seconds=max(0,(parsedate_to_datetime(delay)-datetime.now(timezone.utc)).total_seconds())
                if seconds>20 or attempt==2:raise SourceError('图片限流，延后配图','blocked')
                time.sleep(seconds);continue
            break
        if response.is_redirect:
            from urllib.parse import urljoin
            url=urljoin(url,response.headers['Location']);response.close();continue
        response.raise_for_status()
        try:
            if response.headers.get('Content-Type','').split(';')[0] not in {'image/png','image/jpeg','image/webp'}:
                raise ValueError('图片格式不支持')
            chunks=[];size=0
            for chunk in response.iter_content(65536):
                size+=len(chunk)
                if size>MAX_DOWNLOAD:raise ValueError('图片超出下载上限')
                chunks.append(chunk)
            return b''.join(chunks)
        finally:response.close()
    raise ValueError('图片重定向过多')


def normalize(raw):
    with Image.open(io.BytesIO(raw)) as image:
        image.load()
        if image.format not in {'PNG','JPEG','WEBP'}:raise ValueError('实际图片格式不支持')
        image=image.convert('RGB');image.thumbnail((2000,2000))
        result=io.BytesIO();image.save(result,format='WEBP',quality=90,method=6)
        if result.tell()>2*1024*1024:raise ValueError('图片无法压缩到合理大小')
        return result.getvalue(),image.size


def redraw(f):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    rows=f['data'];fig,ax=plt.subplots(figsize=(8,3.8),layout='constrained')
    ax.barh([r['label'] for r in rows],[r['value'] for r in rows],color='#527568')
    ax.invert_yaxis();ax.set_xlabel(f.get('unit',''));ax.set_title(f.get('chart_title',''))
    for i,row in enumerate(rows):ax.text(row['value'],i,'  '+str(row['value']),va='center',fontsize=10)
    ax.spines[['top','right']].set_visible(False);ax.set_facecolor('white')
    output=io.BytesIO();fig.savefig(output,format='png',dpi=180,facecolor='white');plt.close(fig)
    return output.getvalue()


def prepare(root,card):
    f=card.get('figure')
    if not f:return card
    try:
        validate_descriptor(f)
        if not card.get('focus'):raise ValueError('仅重点论文配图')
        if f['kind']=='link':return card
        if f.get('asset'):
            asset_bytes(root,f);return card
        raw=download(f['image_url']) if f['kind']=='original' else redraw(f)
        raw,size=normalize(raw);digest=hashlib.sha256(raw).hexdigest()
        relative='assets/figures/'+digest+'.webp';dest=root/'state/tech/media'/Path(relative).name
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        f.update(asset=relative,sha256=digest,width=size[0],height=size[1],bytes=len(raw))
        if len(raw)>800*1024:card['media_warning']='配图为保留可读性超过800 KB目标'
    except (ValueError,OSError,KeyError,requests.RequestException,RuntimeError) as exc:
        card.pop('figure',None);card['media_warning']='配图未就绪，降级为文字：'+str(exc)
    return card


def asset_bytes(root,f):
    asset=f.get('asset','')
    if not re.fullmatch(r'assets/figures/[a-f0-9]{64}\.webp',asset):raise ValueError('图片资源路径无效')
    for path in (root/'state/tech/media'/Path(asset).name,root/'docs'/asset):
        if path.is_file():
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=f.get('sha256'):raise ValueError('图片哈希不一致')
            if Path(asset).name!=f.get('sha256','')+'.webp':raise ValueError('资源名与哈希不一致')
            with Image.open(io.BytesIO(raw)) as im:
                if not all(type(f.get(k)) is int and f[k]>0 for k in ('width','height')) or im.size!=(f['width'],f['height']):
                    raise ValueError('图片尺寸与资源不一致')
                if im.format!='WEBP':raise ValueError('归档图片格式无效')
                im.verify()
            return raw
    raise ValueError('图片资源不存在')


def public_assets(root,cards,directory):
    paths=[]
    for card in cards:
        f=card.get('figure',{})
        if not f.get('asset'):continue
        raw=asset_bytes(root,f);path=directory/f['asset'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        paths.append('docs/'+f['asset'])
    return paths


def remote_ready(base_url,cards):
    style=(Path(__file__).parent/'web/digest.css').read_bytes()+b'\n'+(Path(__file__).parent/'web/quality.css').read_bytes()
    response=requests.get(base_url.rstrip('/')+'/assets/digest-v3.css?v='+hashlib.sha256(style).hexdigest()[:12],timeout=15,headers={'Cache-Control':'no-cache'})
    if response.status_code!=200 or hashlib.sha256(response.content).digest()!=hashlib.sha256(style).digest():return False
    for card in cards:
        f=card.get('figure',{})
        if f.get('asset'):
            response=requests.get(base_url.rstrip('/')+'/'+f['asset'],timeout=15)
            if response.status_code!=200 or hashlib.sha256(response.content).hexdigest()!=f['sha256']:return False
    return True
