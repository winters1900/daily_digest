"""技术来源凭据仅保存在系统钥匙串，不写入公开仓库。"""
import getpass
import os
import sys

import keyring

SERVICE = 'daily-digest-tech'
NAME = 'semantic_scholar_api_key'


def semantic_key():
    value = os.environ.get('DAILY_DIGEST_S2_API_KEY', '').strip()
    if value:
        return value
    try:
        return keyring.get_password(SERVICE, NAME)
    except Exception:
        raise RuntimeError('无法读取系统钥匙串中的 Semantic Scholar 凭据') from None


def main():
    if not sys.stdin.isatty():
        print('请在本机交互式终端运行此命令，以隐藏输入')
        return 1
    try:
        value = getpass.getpass('粘贴 Semantic Scholar API Key（输入隐藏）：').strip()
        if not value:
            print('未输入 Key，未修改凭据')
            return 1
        keyring.set_password(SERVICE, NAME, value)
    except (EOFError, KeyboardInterrupt):
        print('\n已取消，未修改凭据')
        return 1
    except Exception:
        print('凭据保存失败，请检查系统钥匙串是否可用')
        return 1
    print('已保存到系统钥匙串；尚未验证 API 是否接受此 Key')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
