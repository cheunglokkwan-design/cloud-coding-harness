# Roblox fleet viewer (optional)

A small Roblox experience that shows the harness's agents as a 3D scene. Each agent is a pod
that changes colour as it moves through provision → analyze → generate → test → deliver. Its
orb bobs while the agent works and sparkles when the agent succeeds. A scoreboard shows live
totals.

The harness itself does **not** run inside Roblox. Roblox servers can only make outbound HTTPS
requests to public URLs, so the viewer polls a small read-only relay that you expose with a
tunnel.

```
harness API (:8080)  ←poll─  scripts/fleet_relay.py (:8787, token)  ←tunnel─  Roblox server (HttpService)
```

> This viewer has not been play-tested in Roblox Studio by the author yet. If something doesn't
> work, please open an issue.

## Setup

1. Run the harness (`docker compose up` or `make api`) and launch some agents.
2. Start the relay. It serves only `/fleet` and checks a token, so the API that can launch
   agents is never exposed:
   ```bash
   TOKEN=$(openssl rand -hex 16); echo $TOKEN
   python scripts/fleet_relay.py --api http://localhost:8080 --port 8787 --token $TOKEN
   cloudflared tunnel --url http://localhost:8787      # or: ngrok http 8787
   ```
3. In Roblox Studio: **Game Settings → Security → Allow HTTP Requests** = on.
4. Put the code into the place, using either method:
   - **Rojo:** `rojo serve integrations/roblox/default.project.json` and connect from the Rojo plugin.
   - **By hand:** create `ReplicatedStorage/Shared/Config` (ModuleScript) and
     `ServerScriptService/FleetViewer` (Script) and paste in the two files from `src/`.
5. In `Config`, set `FLEET_URL` to `https://<your-tunnel>/fleet` and `FLEET_TOKEN` to your token,
   then press **Play**.

## Notes

- `HttpService` allows 500 requests per minute per server. The default poll every 2 s uses 30.
- The token is kept in a server-side script and is never sent to players' clients. Don't put it in
  a LocalScript.
- Don't publish the place with a real token in it if other people can edit it.
