--!strict
-- Polls the harness fleet relay and renders every agent as a "pod" in the workspace:
-- a pad coloured by phase with a floating orb that bobs while the agent is working,
-- plus a scoreboard with live totals. Runs on the server; parts replicate to all players.

local HttpService = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local TweenService = game:GetService("TweenService")

local Config = require(ReplicatedStorage:WaitForChild("Shared"):WaitForChild("Config"))

local TERMINAL = { succeeded = true, failed = true, error = true, cancelled = true }

local root = Instance.new("Folder")
root.Name = "AgentFleet"
root.Parent = workspace

type Run = {
	id: string,
	status: string,
	phase: string?,
	task: string,
	turns: number,
	tests_passed: boolean?,
	files_changed: number,
}

type Pod = { model: Model, pad: BasePart, orb: BasePart, label: TextLabel, state: string, slot: number }

local pods: { [string]: Pod } = {}
local order: { string } = {}

local function stateOf(run: Run): string
	if TERMINAL[run.status] then
		return run.status
	end
	return run.phase or run.status
end

local function slotPosition(slot: number): Vector3
	local col = (slot - 1) % Config.COLUMNS
	local row = math.floor((slot - 1) / Config.COLUMNS)
	return Vector3.new(col * Config.SPACING, 0.5, row * Config.SPACING)
end

local function makePod(run: Run, slot: number): Pod
	local model = Instance.new("Model")
	model.Name = run.id

	local pad = Instance.new("Part")
	pad.Name = "Pad"
	pad.Anchored = true
	pad.Size = Vector3.new(7, 1, 7)
	pad.Material = Enum.Material.Neon
	pad.Position = slotPosition(slot)
	pad.Parent = model

	local orb = Instance.new("Part")
	orb.Name = "Agent"
	orb.Shape = Enum.PartType.Ball
	orb.Anchored = true
	orb.CanCollide = false
	orb.Size = Vector3.new(2.5, 2.5, 2.5)
	orb.Material = Enum.Material.Glass
	orb.Transparency = 0.2
	orb.Position = pad.Position + Vector3.new(0, 3, 0)
	orb.Parent = model

	local gui = Instance.new("BillboardGui")
	gui.Size = UDim2.fromOffset(180, 46)
	gui.StudsOffset = Vector3.new(0, 3, 0)
	gui.MaxDistance = 80
	gui.AlwaysOnTop = false
	gui.Parent = orb

	local label = Instance.new("TextLabel")
	label.Size = UDim2.fromScale(1, 1)
	label.BackgroundTransparency = 0.35
	label.BackgroundColor3 = Color3.fromRGB(18, 21, 27)
	label.TextColor3 = Color3.new(1, 1, 1)
	label.TextScaled = true
	label.Font = Enum.Font.Code
	label.Parent = gui

	model.PrimaryPart = pad
	model.Parent = root
	return { model = model, pad = pad, orb = orb, label = label, state = "", slot = slot }
end

local function applyRun(pod: Pod, run: Run)
	local state = stateOf(run)
	pod.label.Text = string.format("%s · %s\n%s", string.sub(run.id, 5, 12), state, string.sub(run.task, 1, 40))
	if state == pod.state then
		return
	end
	pod.state = state
	local color = Config.PHASE_COLORS[state] or Config.PHASE_COLORS.queued
	local info = TweenInfo.new(0.4)
	TweenService:Create(pod.pad, info, { Color = color }):Play()
	TweenService:Create(pod.orb, info, { Color = color }):Play()
	if state == "succeeded" then
		local sparkles = Instance.new("Sparkles")
		sparkles.SparkleColor = color
		sparkles.Parent = pod.orb
		task.delay(3, function()
			sparkles.Enabled = false
		end)
	end
end

-- Scoreboard ------------------------------------------------------------------
local board = Instance.new("Part")
board.Name = "Scoreboard"
board.Anchored = true
board.Size = Vector3.new(40, 12, 1)
board.Position = Vector3.new((Config.COLUMNS - 1) * Config.SPACING / 2, 10, -14)
board.Color = Color3.fromRGB(12, 13, 17)
board.Parent = root
local surface = Instance.new("SurfaceGui")
surface.Face = Enum.NormalId.Back
surface.Parent = board
local stats = Instance.new("TextLabel")
stats.Size = UDim2.fromScale(1, 1)
stats.BackgroundTransparency = 1
stats.TextColor3 = Color3.fromRGB(230, 233, 239)
stats.TextScaled = true
stats.Font = Enum.Font.Code
stats.Text = "connecting to harness…"
stats.Parent = surface

-- Polling ---------------------------------------------------------------------
local function fetch(): any?
	local ok, body = pcall(function()
		return HttpService:GetAsync(Config.FLEET_URL, true, { ["X-Fleet-Token"] = Config.FLEET_TOKEN })
	end)
	if not ok then
		warn("[fleet] request failed:", body)
		stats.Text = "harness unreachable\n" .. string.sub(tostring(body), 1, 60)
		return nil
	end
	local decoded, data = pcall(HttpService.JSONDecode, HttpService, body)
	return if decoded then data else nil
end

local function render(snapshot: any)
	local runs: { Run } = snapshot.runs or {}
	local seen: { [string]: boolean } = {}
	-- Oldest first so slots stay stable as new agents arrive.
	for i = #runs, math.max(1, #runs - Config.MAX_PODS + 1), -1 do
		local run = runs[i]
		seen[run.id] = true
		local pod = pods[run.id]
		if not pod then
			table.insert(order, run.id)
			pod = makePod(run, #order)
			pods[run.id] = pod
		end
		applyRun(pod, run)
	end
	for id, pod in pods do
		if not seen[id] then
			pod.model:Destroy()
			pods[id] = nil
		end
	end
	stats.Text = string.format(
		"AGENT FLEET\nrunning %d · queued %d · peak %d\nsucceeded %d · failed %d · startup p50 %s",
		snapshot.running or 0, snapshot.queued or 0, snapshot.peak or 0,
		snapshot.succeeded or 0, snapshot.failed or 0,
		snapshot.startup_p50_ms and string.format("%d ms", snapshot.startup_p50_ms) or "–"
	)
end

-- Orbs bob while their agent is working.
RunService.Heartbeat:Connect(function()
	local t = os.clock()
	for _, pod in pods do
		if not TERMINAL[pod.state] then
			local base = pod.pad.Position + Vector3.new(0, 3, 0)
			pod.orb.Position = base + Vector3.new(0, math.sin(t * 3 + pod.slot) * 0.6, 0)
		end
	end
end)

while true do
	local snapshot = fetch()
	if snapshot then
		render(snapshot)
	end
	task.wait(Config.POLL_SECONDS)
end
