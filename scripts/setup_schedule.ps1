# 注册「定期抓取 arXiv 最新论文」计划任务（Windows 计划任务）
#
# 用法（在宿主机 PowerShell 中执行，需 Docker Desktop 已启动）：
#   powershell -ExecutionPolicy Bypass -File scripts\setup_schedule.ps1 `
#       -Query "retrieval augmented generation" -Days 7 -Limit 10 -Time 09:00
#
# 查看：Get-ScheduledTask -TaskName research-copilot-fetch-arxiv
# 立即跑一次：Start-ScheduledTask -TaskName research-copilot-fetch-arxiv
# 取消：Unregister-ScheduledTask -TaskName research-copilot-fetch-arxiv -Confirm:$false
param(
    [string]$Query = "retrieval augmented generation",
    [int]$Days = 7,
    [int]$Limit = 10,
    [string]$Time = "09:00",
    [string]$TaskName = "research-copilot-fetch-arxiv",
    [string]$Container = "research-copilot-backend",
    [switch]$DryRun
)

$argLine = "--query `"$Query`" --days $Days --limit $Limit"
$cmd = "docker exec $Container python /app/scripts/fetch_papers.py $argLine"
Write-Host "命令：$cmd"
Write-Host "计划：每天 $Time"

if ($DryRun) {
    Write-Host "[DryRun] 未注册任务。"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
# 笔记本电池供电时也运行，否则任务会被静默跳过
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "科研小助手：定期抓取 arXiv 最新论文并入库" -Force | Out-Null

Write-Host "已注册计划任务：$TaskName"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
