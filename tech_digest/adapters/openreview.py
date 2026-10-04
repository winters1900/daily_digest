"""官方 OpenReview API V2：接受状态与事件时间分离保存。"""
import re
from datetime import datetime,timezone,timedelta

from .public import entry,SourceError

API='https://api2.openreview.net/notes'


def value(note,key):
    field=note.get('content',{}).get(key)
    return field.get('value') if isinstance(field,dict) else field


def event_date(ms,now):
    if isinstance(ms,bool) or not isinstance(ms,(float,int)) or ms<=0:return None
    try:dt=datetime.fromtimestamp(ms/1000,timezone.utc).astimezone(now.tzinfo)
    except (ValueError,OverflowError,OSError):return None
    return dt if dt<=now+timedelta(minutes=5) else None


def candidate(source,note,now,group):
    uid=note.get('id','');forum=note.get('forum') or uid
    if not re.fullmatch(r'[A-Za-z0-9_-]+',str(uid)) or uid!=forum or note.get('ddate'):return None
    title=value(note,'title')
    if not isinstance(title,str) or not title:return None
    url='https://openreview.net/forum?id='+uid
    accepted=value(note,'venueid')==group
    events=[];pdate=event_date(note.get('pdate'),now)
    if accepted and pdate:
        events.append(dict(type='acceptance',date=pdate.date().isoformat(),at=pdate.isoformat(),url=url,
            locator='submission.content.venueid / submission.pdate',
            excerpt='venueid='+group+'; pdate='+str(note['pdate']),date_field='pdate',
            venue=source['venues'][0],track='journal' if source['type']=='journal' else 'main'))
    # Decision只能来自所属会刊的官方邀请与签名，作者自述不算决定。
    replies=note.get('details',{}).get('replies',[])
    for reply in replies if isinstance(replies,list) else []:
        invites=reply.get('invitations',[])
        official=any(i.startswith(group+'/') and re.search(r'/-/(?:Decision|Acceptance_Decision)$',i) for i in invites)
        signed=any(s==group or s.startswith(group+'/') for s in reply.get('signatures',[]))
        decision=value(reply,'decision')
        if not official or not signed or reply.get('forum')!=uid or not isinstance(decision,str):continue
        if not re.match(r'^Accept(?:\b|\s*\()',decision,re.I):continue
        # cdate用于决定显示日期；tcdate单独保留，mdate不刷新录用时间。
        date=event_date(reply.get('cdate'),now)
        if date:
            events.append(dict(type='acceptance',date=date.date().isoformat(),at=date.isoformat(),url=url+'&noteId='+reply['id'],
                locator='Decision.content.decision / Decision.cdate',excerpt=decision,date_field='cdate',
                tcdate=reply.get('tcdate'),venue=source['venues'][0],track='journal' if source['type']=='journal' else 'main'))
    missing=[]
    if not accepted:missing.append('官方 accepted venueid')
    if not events:missing.append('录用/发表精确日期')
    first=event_date(note.get('odate'),now)
    result=entry(source,title,url,abstract=value(note,'abstract') or '',authors=value(note,'authors') or [],
        openreview_id=uid,official_events=events,publication_missing=missing,
        verification_url=url,verification_note='官方 API 状态和事件日期已提取；仍须核对论文类型、轨道与原文，非摘要审核完成',
        official_metadata={'venueid':value(note,'venueid'),'venue':value(note,'venue'),'pdate':note.get('pdate'),
            'cdate':note.get('cdate'),'tcdate':note.get('tcdate'),'odate':note.get('odate')})
    # odate可由会刊回填，不能代替首次公开日期；若有arXiv，后续核验更早版本。
    if first:result['openreview_public_at']=first.isoformat()
    if events:result['accepted_at']=events[0]['at'];result['accepted_date']=events[0]['date']
    return result


def collect(source,http,now,progress):
    groups=source['openreview_groups'];cursor=dict(progress.get('cursor',{}))
    scan=dict(cursor.get('openreview_scan',{}));result=[];visited=[];failure=None
    from ..__main__ import window_start
    cutoff=window_start(now,2).date().isoformat()
    for group in groups:
        state=dict(scan.get(group,{}));offset=state.get('offset',0)
        if state.get('complete') and state.get('day')==now.date().isoformat():continue
        if state.get('complete'):offset=0
        limit=source.get('detail_budget',12)
        try:
            response=http.get(API,params={'content.venueid':group,'limit':limit,'offset':offset,'sort':'pdate:desc','details':'replies'})
            payload=response.json()
            if not isinstance(payload.get('notes'),list) or not isinstance(payload.get('count'),int):raise SourceError('OpenReview响应缺少 notes/count，游标未推进')
            notes=payload['notes'];batch=[candidate(source,n,now,group) for n in notes]
            result.extend(c for c in batch if c)
            visited.append(API+'?content.venueid='+group)
            # 接受日倒序：遇到窗口以前且全批有可靠接受日，后续可停止；缺日期仍需继续。
            before=bool(batch) and all(c and c.get('accepted_date') and c['accepted_date']<cutoff for c in batch)
            complete=offset+len(notes)>=payload['count'] or before
            if not notes and offset<payload['count']:raise SourceError('OpenReview空页与count不一致，未推进游标')
            scan[group]={'offset':offset+len(notes),'complete':complete,'day':now.date().isoformat(),
                         'checked_order':'pdate:desc','stopped_before_window':before,'window_start':cutoff}
        except (ValueError,TypeError):failure=SourceError('OpenReview响应不是有效JSON');break
        except SourceError as exc:failure=exc;break
    cursor['openreview_scan']=scan
    if failure and failure.retry_at:cursor['retry_not_before']=failure.retry_at
    return result,dict(status=failure.status if failure else 'ok',coverage='sample',cursor=cursor,
        evidence_urls=visited or [API],browser_required=source['url'],
        note=('OpenReview官方接口受阻：'+str(failure)+'；最终决定改由正常浏览器核验，未完成区间保留' if failure else
              '按官方accepted venueid分页发现 %d 条，状态与原始pdate/Decision日期分别保存；轨道和首次公开仍逐条审核'%len(result)))
