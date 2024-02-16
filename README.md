```
fly auth login
fly apps create uptime-kuma
cat .env | fly secrets import
fly volumes create app_data --size 1
fly deploy
fly ssh console
```