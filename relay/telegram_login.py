"""Local interactive login with QR or an explicitly reported delivery channel."""
import asyncio
import getpass
import tempfile
import webbrowser
from pathlib import Path

from telethon import errors, types


async def password_login(client):
    await client.sign_in(password=getpass.getpass("Пароль двухэтапной проверки: "))


async def login(client):
    await client.connect()
    if await client.is_user_authorized():
        print("Используется сохранённая Telegram-сессия.")
        return
    mode = input("Вход: 1 = QR-код (рекомендуется), 2 = код по номеру [1]: ").strip()
    if mode in ("", "1"):
        import qrcode
        import qrcode.image.svg
        print("На телефоне: Telegram → Настройки → Устройства → Подключить устройство.")
        print("Сканируй QR-код в открывшемся браузере. Подтверди вход на телефоне.")
        with tempfile.TemporaryDirectory(prefix="relay-login-") as directory:
            path = Path(directory) / "telegram-login.svg"
            qr = await client.qr_login()
            for attempt in range(3):
                image = qrcode.make(
                    qr.url, image_factory=qrcode.image.svg.SvgPathFillImage, border=4
                )
                image.save(str(path))
                webbrowser.open(path.as_uri())
                print("QR-код открыт локально. Если браузер не открылся, открой файл:")
                print(path)
                try:
                    await qr.wait()
                    return
                except errors.SessionPasswordNeededError:
                    await password_login(client)
                    return
                except asyncio.TimeoutError:
                    if attempt == 2:
                        raise RuntimeError("Срок QR-кода истёк. Перезапусти скрипт.")
                    print("QR-код истёк; обновляю. Сканируй новый код.")
                    await qr.recreate()
        return
    if mode != "2":
        raise RuntimeError("Выбери способ входа 1 или 2.")
    phone = input("Номер Telegram в международном формате: ").strip()
    try:
        sent = await client.send_code_request(phone)
    except errors.SessionPasswordNeededError:
        await password_login(client)
        return
    if isinstance(sent, types.auth.SentCodeSuccess):
        return
    channel = type(getattr(sent, "type", None)).__name__
    descriptions = {
        "SentCodeTypeApp": "Код отправлен в служебный чат Telegram на уже подключённых устройствах (не SMS).",
        "SentCodeTypeSms": "Telegram выбрал отправку SMS на указанный номер.",
        "SentCodeTypeCall": "Telegram выбрал телефонный звонок с кодом.",
        "SentCodeTypeEmailCode": "Telegram выбрал отправку на привязанную электронную почту.",
    }
    print(descriptions.get(channel, f"Способ доставки от Telegram: {channel}."))
    if channel not in descriptions:
        raise RuntimeError("Этот способ входа не поддерживается скриптом. Перезапусти и выбери QR-код.")
    print("Если кода нет, нажми Ctrl+C и перезапусти скрипт с выбором QR-кода.")
    code = getpass.getpass("Код входа (символы не отображаются): ").strip()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except errors.SessionPasswordNeededError:
        await password_login(client)

