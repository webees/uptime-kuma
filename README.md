# 1️⃣ Log in to your Fly.io account (opens browser authentication)
fly auth login

# 2️⃣ Create the Fly.io application (app name: uptime-kuma)
fly apps create uptime-kuma

# 3️⃣ Import all environment variables from .env into Fly Secrets
cat .env | fly secrets import

# 4️⃣ Create a persistent volume named app_data with 1GB storage
fly volumes create app_data --size 1

# 5️⃣ Deploy the current project to Fly.io (based on fly.toml configuration)
fly deploy

# 6️⃣ Open an SSH console into the running Fly.io instance for debugging
fly ssh console
