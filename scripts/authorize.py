"""Gera a StringSession (correr UMA vez, no teu computador, nunca no GitHub).
Uso: TG_API_ID=... TG_API_HASH=... python scripts/authorize.py
Copia o valor impresso para o Secret TG_SESSION. É acesso total à conta: não o partilhes."""
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

with TelegramClient(StringSession(), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH']) as client:
    print('AUTORIZADO. TG_SESSION:\n' + client.session.save() if client.is_user_authorized() else 'NÃO AUTORIZADO')
