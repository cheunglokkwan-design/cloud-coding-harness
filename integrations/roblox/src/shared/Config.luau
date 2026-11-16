--!strict
-- Settings for the fleet viewer. Edit these before pressing Play.
local Config = {
	-- Public URL of scripts/fleet_relay.py (exposed with cloudflared/ngrok).
	-- Roblox servers cannot reach "localhost" on your machine.
	FLEET_URL = "https://YOUR-TUNNEL.trycloudflare.com/fleet",
	-- Must match --token passed to fleet_relay.py. Kept on the server only.
	FLEET_TOKEN = "change-me-to-the-relay-token",
	POLL_SECONDS = 2, -- HttpService allows 500 requests/minute per server
	COLUMNS = 12, -- pods per row
	SPACING = 10, -- studs between pods
	MAX_PODS = 200,
}

-- Colours match the web control plane's legend.
Config.PHASE_COLORS = {
	queued = Color3.fromRGB(203, 210, 220),
	starting = Color3.fromRGB(203, 210, 220),
	provision = Color3.fromRGB(147, 197, 253),
	analyze = Color3.fromRGB(96, 165, 250),
	generate = Color3.fromRGB(139, 92, 246),
	test = Color3.fromRGB(245, 158, 11),
	deliver = Color3.fromRGB(20, 184, 166),
	succeeded = Color3.fromRGB(34, 197, 94),
	failed = Color3.fromRGB(239, 68, 68),
	error = Color3.fromRGB(153, 27, 27),
	cancelled = Color3.fromRGB(156, 163, 175),
}

return Config
