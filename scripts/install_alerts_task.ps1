<#
pteropilot-alerts 的 Windows 计划任务一键安装脚本。

为什么需要：README 里给的定时方案是 cron（Linux/macOS），但绝大多数 MC 服主在 Windows 上
（本地开服、Windows 云主机），不会写 cron，也不知道「任务计划程序」怎么填。
这个脚本把「装计划任务 → 写参数 → 记日志 → 查状态 → 卸载」全包了。

用法（PowerShell）：
  # 安装：每 5 分钟检查一次，异常推钉钉机器人
  powershell -ExecutionPolicy Bypass -File scripts\install_alerts_task.ps1 `
      -Webhook "https://oapi.dingtalk.com/robot/send?access_token=***"

  # 每 15 分钟、失败才告警（只报 error 态）
  ... -IntervalMinutes 15 -MinLevel error -Webhook "https://..."

  # 邮件通道（需要 smtp 授权码）
  ... -SmtpHost smtp.qq.com -SmtpUser you@qq.com -SmtpPass <授权码> -MailTo ops@x.com

  # 只打印将要执行的命令，不真的安装
  ... -Webhook "https://..." -DryRun

  # 查看任务状态 / 卸载
  ... -Status
  ... -Remove

前置：已安装 pteropilot（`uv tool install pteropilot` 或 `pip install pteropilot`），
并且设置好 PANEL_URL / PANEL_API_KEY（脚本会提示检查）。

脚本做的事：
  1. 校验 pteropilot-alerts 可执行 + 面板环境变量就绪（可 -SkipPreflight 跳过）
  2. 生成运行包装脚本 %USERPROFILE%\.pteropilot\run-alerts.ps1（把 webhook/SMTP 等参数写进去，
     避免密钥出现在计划任务命令行里、也绕开 schtasks 的引号转义坑）
  3. 建 Windows 计划任务（默认每 5 分钟，开机后自动生效，需本机登录后可运行）
  4. 日志写到 %USERPROFILE%\.pteropilot\alerts.log（每次运行追加，含时间戳）
  5. 支持 -Status / -Remove / -DryRun
#>

param(
    [string]$Webhook = "",
    [string]$MinLevel = "",
    [int]$IntervalMinutes = 5,
    [int]$Cooldown = 0,
    [int]$ConfirmCount = 0,
    [string]$Token = "",
    [string]$SmtpHost = "",
    [int]$SmtpPort = 465,
    [string]$SmtpUser = "",
    [string]$SmtpPass = "",
    [string]$MailTo = "",
    [string]$MailFrom = "",
    [string]$Exe = "",
    [switch]$NoLogSignals,
    [switch]$SkipPreflight,
    [switch]$Status,
    [switch]$Remove,
    [switch]$DryRun,
    [string]$TaskName = "pteropilot-alerts"
)

$ErrorActionPreference = "Stop"

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Ok($msg) { Write-Host "  ✅ $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  ⚠️  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "  ❌ $msg" -ForegroundColor Red }

$stateDir = Join-Path $env:USERPROFILE ".pteropilot"
$wrapper = Join-Path $stateDir "run-alerts.ps1"
$logFile = Join-Path $stateDir "alerts.log"

# ---------------- 状态 ----------------
function Show-Status {
    Say ""
    Say "== 计划任务状态：$TaskName ==" "Cyan"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Warn "任务不存在（还没安装）。运行本脚本（带 -Webhook 或邮件参数）即可安装。"
        return 1
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    Ok "任务已安装"
    Say ("  状态：     " + $task.State)
    if ($info) {
        Say ("  上次运行： " + $info.LastRunTime)
        Say ("  上次结果： " + $info.LastTaskResult + $(if ($info.LastTaskResult -eq 0) { "（成功）" } else { "（失败：非 0 需查日志）" }))
        Say ("  下次运行： " + $info.NextRunTime)
    }
    Say ("  重复间隔： 每 " + (($task.Triggers | Select-Object -First 1).Repetition.Interval) )

    Say ""
    if (Test-Path $wrapper) { Ok "包装脚本：$wrapper" } else { Warn "包装脚本缺失：$wrapper（建议重新安装）" }
    if (Test-Path $logFile) {
        $size = [math]::Round((Get-Item $logFile).Length / 1KB, 1)
        Ok "日志：$logFile（$size KB）"
        Say ""
        Say "  最近 10 行日志：" "Cyan"
        Get-Content $logFile -Tail 10 | ForEach-Object { Say "    $_" }
    } else {
        Warn "还没有日志（任务尚未运行过）。可立刻触发一次：schtasks /run /tn `"$TaskName`""
    }
    return 0
}

# ---------------- 卸载 ----------------
function Remove-Task {
    Say ""
    Say "== 卸载计划任务：$TaskName ==" "Cyan"
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Ok "任务已删除"
    } else {
        Warn "任务不存在（可能本来就没装）"
    }
    if (Test-Path $wrapper) {
        Remove-Item $wrapper -Force
        Ok "包装脚本已删除：$wrapper"
    }
    Say ""
    Say "  日志保留在 $logFile（如不再需要可手动删除）" "DarkGray"
    return 0
}

# ---------------- 主流程 ----------------
if ($Status) { exit (Show-Status) }
if ($Remove) { exit (Remove-Task) }

Say ""
Say "== 安装 pteropilot-alerts 计划任务（每 $IntervalMinutes 分钟）==" "Cyan"
if ($DryRun) { Warn "DryRun 模式：只打印，不创建任何东西" }

# -- 1. 找到 pteropilot-alerts --
$exe = $null
if ($Exe) {
    if (-not (Test-Path $Exe)) { Fail "-Exe 指定的路径不存在：$Exe"; exit 1 }
    $exe = (Resolve-Path $Exe).Path
} else {
    $cmd = Get-Command "pteropilot-alerts" -ErrorAction SilentlyContinue
    if ($cmd) {
        $exe = $cmd.Source
    } else {
        # 常见位置兜底：uv tool / pipx / 本仓库 venv（开发调试时）
        $repoRoot = Split-Path $PSScriptRoot -Parent
        $cands = @(
            (Join-Path $env:USERPROFILE ".local\bin\pteropilot-alerts.exe"),
            (Join-Path $env:USERPROFILE ".local\bin\pteropilot-alerts"),
            (Join-Path $env:APPDATA "uv\tools\pteropilot\Scripts\pteropilot-alerts.exe"),
            (Join-Path $repoRoot ".venv\Scripts\pteropilot-alerts.exe")
        )
        foreach ($c in $cands) { if (Test-Path $c) { $exe = $c; break } }
    }
}
if (-not $exe) {
    Fail "找不到 pteropilot-alerts 命令。请先安装："
    Say "     uv tool install pteropilot      （或 pip install pteropilot）" "DarkGray"
    Say "     装完重开一个 PowerShell 窗口再跑本脚本" "DarkGray"
    Say "     已经装在别处的话，用 -Exe `"完整路径\pteropilot-alerts.exe`" 指定" "DarkGray"
    exit 1
}
Ok "找到命令：$exe"

# -- 2. 面板凭据检查 --
if (-not $SkipPreflight) {
    if (-not $env:PANEL_URL -or -not $env:PANEL_API_KEY) {
        Warn "当前窗口没有设置 PANEL_URL / PANEL_API_KEY 环境变量。"
        Say "     计划任务在后台运行时读不到这两个变量就会失败。两种做法：" "DarkGray"
        Say "     A) 在系统里永久设置：setx PANEL_URL `"https://panel.example.com`"  → 重开窗口" "DarkGray"
        Say "        setx PANEL_API_KEY `"ptlc_xxx`"" "DarkGray"
        Say "     B) 确认你已在系统环境变量里配好，用 -SkipPreflight 跳过本检查" "DarkGray"
        Say ""
        Say "     如果只是想先看看效果，可以加 -DryRun。" "Yellow"
        exit 1
    }
    Ok "面板凭据已设置（PANEL_URL=$env:PANEL_URL）"
}

# -- 3. 必须至少一种通知渠道 --
$hasWebhook = [bool]$Webhook
$hasMail = [bool]($SmtpHost -and $SmtpUser -and $MailTo)
if (-not $hasWebhook -and -not $hasMail) {
    Fail "至少要有一种通知渠道：-Webhook <地址>，或 -SmtpHost/-SmtpUser/-SmtpPass/-MailTo"
    Say "     例：-Webhook `"https://oapi.dingtalk.com/robot/send?access_token=xxx`"" "DarkGray"
    exit 1
}

# 面板凭据（写进包装脚本，任务在后台运行也能读到）
$panelUrl = if ($env:PANEL_URL) { $env:PANEL_URL } else { "" }
$panelApiKey = if ($env:PANEL_API_KEY) { $env:PANEL_API_KEY } else { "" }

# -- 4. 生成包装脚本 --
# 注意：PowerShell 数组里不能裸写 "--webhook url"（`--` 会被当运算符），
# 所以每个 token 单独一行、用单引号包住。
$argList = New-Object System.Collections.ArrayList
function Add-Arg([string]$name, [string]$value) {
    $q = $value -replace "'", "''"      # PS 单引号字符串里 ' → ''
    if ($value -or $null -ne $value) {
        [void]$argList.Add("    '$name'")
        [void]$argList.Add("    '$q'")
    }
}

if ($hasWebhook) { Add-Arg "--webhook" $Webhook }
if ($MinLevel) { Add-Arg "--min-level" $MinLevel }
if ($Cooldown -gt 0) { Add-Arg "--cooldown" "$Cooldown" }
if ($ConfirmCount -gt 0) { Add-Arg "--confirm-count" "$ConfirmCount" }
if ($Token) { Add-Arg "--token" $Token }
if ($hasMail) {
    Add-Arg "--smtp-host" $SmtpHost
    Add-Arg "--smtp-port" "$SmtpPort"
    Add-Arg "--smtp-user" $SmtpUser
    if ($SmtpPass) { Add-Arg "--smtp-pass" $SmtpPass }
    Add-Arg "--mail-to" $MailTo
    if ($MailFrom) { Add-Arg "--mail-from" $MailFrom }
}
if ($NoLogSignals) { [void]$argList.Add("    '--no-log-signals'") }

$argBlock = if ($argList.Count -gt 0) { ($argList -join "`n") } else { "" }

$wrapperBody = @"
# 由 install_alerts_task.ps1 生成于 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
# 作用：把 pteropilot-alerts 的参数与日志重定向包成一次运行，计划任务只调用本脚本。
# 参数改动请重跑安装脚本（不要手工改这里）。

`$log  = "$logFile"
`$exe  = "$exe"

# 面板凭据：计划任务在后台运行看不到当前会话的环境变量，所以直接写进来。
# 文件在用户目录（仅本机本人可读），比在命令行暴露或依赖 setx 更省事。
`$env:PANEL_URL     = "$panelUrl"
`$env:PANEL_API_KEY = "$panelApiKey"
# 中文日志不乱码
`$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
`$alertsArgs = @(
$argBlock
)

`$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
('' + [Environment]::NewLine + '===== ' + `$stamp + ' =====') | Out-File -FilePath `$log -Append -Encoding UTF8
& `$exe @alertsArgs 2>&1 | Out-File -FilePath `$log -Append -Encoding UTF8
('exit=' + `$LASTEXITCODE) | Out-File -FilePath `$log -Append -Encoding UTF8
"@
$wrapperBody = $wrapperBody -replace "`r`n", "`n"

Say ""
Say "  将生成的包装脚本（$wrapper）：" "Cyan"
$wrapperBody -split "`n" | ForEach-Object { Say "    $_" "DarkGray" }

if ($DryRun) {
    Say ""
    Say "  （DryRun）将用 ScheduledTasks 创建：" "Cyan"
    Say "    动作：powershell.exe -NoProfile -WindowStyle Hidden -File `"$wrapper`"" "DarkGray"
    Say "    触发：立即开始，每 $IntervalMinutes 分钟重复" "DarkGray"
    Say ""
    Warn "DryRun 结束，未创建任何东西。"
    exit 0
}

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
# 包装脚本含中文注释，PS 5.1 需要 UTF-8 BOM
[System.IO.File]::WriteAllText($wrapper, $wrapperBody, (New-Object System.Text.UTF8Encoding($true)))
Ok "包装脚本已写入"

# -- 5. 建计划任务（用 ScheduledTasks cmdlet，避免 schtasks 命令行引号地狱）--
try {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument ('-NoProfile -WindowStyle Hidden -File "{0}"' -f $wrapper)
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Description "pteropilot 崩服告警定时检查（每 $IntervalMinutes 分钟）" -Force | Out-Null
} catch {
    Fail "创建计划任务失败：$($_.Exception.Message)"
    Say "     可手动在「任务计划程序」里建：动作 powershell -File `"$wrapper`"" "DarkGray"
    exit 1
}
Ok "计划任务已创建（每 $IntervalMinutes 分钟运行一次）"

Write-Host ""
Say "== 完成 ==" "Green"
Say "  任务名：$TaskName"
Say "  日志：  $logFile"
Say "  状态：  powershell -File scripts\install_alerts_task.ps1 -Status"
Say "  卸载：  powershell -File scripts\install_alerts_task.ps1 -Remove"
Say ""
Say "  建议先确认一次能跑通（立刻触发一次并看日志）：" "Cyan"
Say "    schtasks /run /tn `"$TaskName`"" "DarkGray"
Say "    Get-Content `"$logFile`" -Tail 20" "DarkGray"
Say ""
Say "  提示：任务默认在「本机登录后」运行；若要未登录也跑，需在任务计划程序里改属性并输入账户密码。" "DarkGray"
exit 0
