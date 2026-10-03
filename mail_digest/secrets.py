"""用系统钥匙串保存授权信息，避免明文落在项目或命令行参数中。"""

import keyring


SERVICE = "daily-digest-mail"


def get_secret(name: str) -> str:
    value = keyring.get_password(SERVICE, name)
    if value is None:
        raise RuntimeError(f"钥匙串中缺少 {name}；请先运行配置命令")
    return value


def put_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)
