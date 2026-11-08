# Удалённый Windows-ПК для спайков (Cloudflare Tunnel + SSH)

ПК сам держит исходящее соединение с Cloudflare, порты на роутере не открываются. Вход по SSH — только по ключу, пароли выключены. Доступ к адресу туннеля дополнительно закрыт Cloudflare Access: подключиться может только тот, у кого есть сервисный токен.

## 1. На ПК (один раз, ~15 мин, от администратора)

1. Скачать скрипт: https://raw.githubusercontent.com/UTALIPOV250618/mojo_studio/feat/phase0-spikes/tools/remote/setup_windows_pc.ps1
2. Открыть PowerShell «Запуск от имени администратора», перейти в папку со скриптом и выполнить (ключ — открытый, его можно пересылать):

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup_windows_pc.ps1 -PublicKey "<открытый ключ из чата>"
   ```

   Скрипт ставит OpenSSH Server (вход только по ключу), Git, Python 3.12, ffmpeg (полная сборка с nvenc/qsv/amf), cloudflared и печатает характеристики ПК. Вывод скрипта прислать в чат.
3. Не выходить из Windows (или включить автовход): плееру S2 нужен рабочий стол вошедшего пользователя. Спящий режим выключить на время тестов.

## 2. В Cloudflare (владелец домена)

1. **Туннель.** Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared → имя `mojo-pc` → вкладка Windows. Скопировать команду `cloudflared.exe service install <токен>` и выполнить её **на ПК** в PowerShell администратора. Токен в чат не присылать. Статус туннеля должен стать «Healthy».
2. **Адрес.** В том же туннеле → Public Hostname → Add: поддомен `pc-ssh`, ваш домен, Service: `SSH`, URL: `localhost:22`.
3. **Защита адреса.** Zero Trust → Access → Service Auth → Service Tokens → Create: имя `mojo-mac`, срок 1 год. Сохранить Client ID и Client Secret (показываются один раз). Затем Access → Applications → Add → Self-hosted: домен `pc-ssh.<ваш домен>`, политика с действием **Service Auth** и правилом «Service Token = mojo-mac».
4. **Токен на Mac.** Не присылая в чат, записать его на Mac в файл (в терминале Mac):

   ```bash
   printf 'TUNNEL_SERVICE_TOKEN_ID=%s\nTUNNEL_SERVICE_TOKEN_SECRET=%s\n' '<Client ID>' '<Client Secret>' > ~/.cloudflared/mojo-pc.env && chmod 600 ~/.cloudflared/mojo-pc.env
   ```

5. Прислать в чат только адрес (`pc-ssh.<ваш домен>`) и имя пользователя Windows из вывода скрипта.

## 3. На Mac (делает Claude)

`~/.ssh/config`:

```
Host mojo-pc
  HostName pc-ssh.<ваш домен>
  User <пользователь Windows>
  IdentityFile ~/.ssh/mojo_pc
  ProxyCommand sh -c 'set -a; . ~/.cloudflared/mojo-pc.env; exec cloudflared access ssh --hostname %h'
```

Проверка: `ssh mojo-pc "hostname; ffmpeg -hide_banner -encoders | findstr /i \"nvenc qsv amf\""`.

## Отключить доступ

Удалить туннель `mojo-pc` в Cloudflare (или на ПК: `cloudflared service uninstall`), удалить строку ключа из `C:\ProgramData\ssh\administrators_authorized_keys`.
