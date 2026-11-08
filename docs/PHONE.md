# Phone-call reminder

If a day is still empty at `CALL_AT` the next day, the bot can have an Asterisk server ring every phone at once and play a short "yesterday is not logged" message, while it sends the card again. It is built for [phone-ai-gateway](https://github.com/AlaaLabban/phone-ai-gateway), but any Asterisk that reads call files works.

## How it works

1. The bot writes `tracker-<day>.call` into `callfiles-tmp/` under `ASTERISK_SPOOL`, then renames it into `outgoing/`, so Asterisk never reads a half-written file.
2. The call file dials `Local/s@tracker-ring`, which rings every extension together. The first to answer takes the call, and Dial cancels the others. An unregistered extension is skipped.
3. The answered phone goes to `[tracker-missed]`, which plays the message twice.
4. Unanswered, Asterisk retries twice, 15 minutes apart. On every tick the bot deletes the call file of any day that has been logged since, which stops retries still pending.

## Dialplan

phone-ai-gateway already has these contexts in `config/extensions.conf`. For another Asterisk, adjust the extensions:

```
[tracker-ring]
exten => s,1,Set(CALLERID(all)="Health log" <100>)
 same => n,Dial(PJSIP/100&PJSIP/101&PJSIP/102,50)
 same => n,Hangup()

[tracker-missed]
exten => s,1,Wait(2)
 same => n,Playback(custom/log-missed)
 same => n,Wait(1)
 same => n,Playback(custom/log-missed)
 same => n,Wait(1)
 same => n,Hangup()
```

The ring length is Dial's timeout (50 s per attempt), not the call file's `WaitTime`.

## The message

Put the recording at `sounds/custom/log-missed.sln16` in Asterisk's sounds folder: raw signed 16-bit mono at 16 kHz. Any text-to-speech voice works, for example Piper and sox:

```bash
echo "Your health log for yesterday is still empty. Please fill it in on Telegram." \
  | piper --model en_US-hfc_female-medium.onnx --output_file msg.wav
sox msg.wav -r 16000 -c 1 -b 16 -e signed-integer -t raw log-missed.sln16
```

## Set-up

- Set `ASTERISK_SPOOL` in `bot.env` to the spool folder (for phone-ai-gateway, its `spool/`). The bot's user needs write access to `callfiles-tmp/` and `outgoing/`.
- Leave `ASTERISK_SPOOL` empty to skip calls; the Telegram reminders still go out.

## Checking it

`grep pbx_spool log/messages.log` shows each call file Asterisk picked up, and `journalctl -u health-bot | grep call` shows what the bot queued and cancelled. An unanswered ring is logged by Asterisk as `Call failed to go through, reason (0) Call Failure`: that is the Local channel ending, not a fault.
