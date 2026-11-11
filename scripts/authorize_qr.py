"""Gera a StringSession por código QR (sem código por mensagem). Correr num ecrã DIFERENTE do telemóvel.

    pip install -r requirements.txt qrcode
    python scripts/authorize_qr.py

No telemóvel: Telegram -> Configurações -> Dispositivos -> Conectar Dispositivo -> apontar ao QR.
Copia a linha final para o Secret TG_SESSION. É acesso total à conta: não a partilhes nem a coles em chats.
"""
import asyncio, getpass

import qrcode
from telethon import TelegramClient, errors
from telethon.sessions import StringSession


def show(url):
    q = qrcode.QRCode(border=2)
    q.add_data(url)
    q.print_ascii(invert=True)


async def main():
    client = TelegramClient(StringSession(), int(input('API ID: ')), getpass.getpass('API HASH (não aparece): '))
    await client.connect()
    qr = await client.qr_login()
    try:
        for attempt in range(6):
            show(qr.url)
            print('Lê este QR no Telegram do telemóvel (tentativa %d de 6, renova a cada ~25 s).' % (attempt + 1))
            try:
                await qr.wait(25)
                break
            except asyncio.TimeoutError:
                await qr.recreate()
            except errors.SessionPasswordNeededError:
                await client.sign_in(password=getpass.getpass('Palavra-passe do Telegram (não aparece): '))
                break
        if await client.is_user_authorized():
            print('\nAUTORIZADO. TG_SESSION:\n' + client.session.save())
        else:
            print('\nNÃO AUTORIZADO: não foi gerada nenhuma sessão. Corre o script outra vez.')
    finally:
        await client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
