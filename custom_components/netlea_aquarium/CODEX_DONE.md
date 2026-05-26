# Netlea Aquarium 插件收尾记录

本轮仅处理当前插件目录：

`D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium`

没有修改 HA 生产配置，没有连接 HA，没有重启 HA，也没有修改其他插件。

## 追加记录：插件品牌图标

后续按用户要求补充了 HA 自定义集成品牌图标资产，新增目录：

`D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium\brand`

新增文件：

- `brand\icon.png`：256x256，亮色主题主图标。
- `brand\icon@2x.png`：512x512，亮色主题高清主图标。
- `brand\dark_icon.png`：256x256，深色主题主图标。
- `brand\dark_icon@2x.png`：512x512，深色主题高清主图标。
- `brand\logo.png`：768x256，亮色主题横向 logo。
- `brand\logo@2x.png`：1536x512，亮色主题高清横向 logo。
- `brand\dark_logo.png`：768x256，深色主题横向 logo。
- `brand\dark_logo@2x.png`：1536x512，深色主题高清横向 logo。

图形设计为“鱼缸轮廓 + 顶部灯带 + 水草 + 小鱼”，横向 logo 文案为 `尼特利鱼缸` / `Netlea Aquarium`。

追加检查：

```powershell
$brand = 'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium\brand'; Add-Type -AssemblyName System.Drawing; Get-ChildItem -LiteralPath $brand -Filter *.png | Sort-Object Name | ForEach-Object { $img = [System.Drawing.Image]::FromFile($_.FullName); [pscustomobject]@{ Name=$_.Name; Width=$img.Width; Height=$img.Height; Bytes=$_.Length }; $img.Dispose() } | Format-Table -AutoSize
```

结果：

```text
Name            Width Height Bytes
----            ----- ------ -----
dark_icon.png      256    256  5717
dark_icon@2x.png   512    512 12602
dark_logo.png      768    256 11757
dark_logo@2x.png  1536    512 28879
icon.png           256    256  5857
icon@2x.png        512    512 12865
logo.png           768    256 11918
logo@2x.png       1536    512 29098
```

并再次确认没有 `__pycache__`、`*.pyc`、`*.pyo`、`*.bak`、`*.tmp`、`*.orig`、`*.rej` 等生成/临时文件。

## 1. 本轮改动文件

- `api.py`
- `coordinator.py`
- `entity.py`
- `light.py`
- `button.py`
- `sensor.py`
- `binary_sensor.py`
- `config_flow.py`
- `manifest.json`
- `strings.json`
- `translations\zh-Hans.json`
- `CODEX_DONE.md`

## 2. 每个文件主要改动

### `api.py`

- 增加 `NetleaControlAckError`，把“控制命令无有效设备回执”视为失败。
- 增加 `as_int`、`as_bool` 等云端字段解析辅助函数，兼容字符串型 `0/1`、布尔值和整数。
- HTTP 请求增加对 `429`、`5xx` 和常见临时云端错误码的重试。
- WebSocket 控制增加目标地址校验、回执地址过滤、关闭/错误帧处理。
- 控制结果增加 `expected_addresses`、`missing_addresses`，便于 HA 里看到哪些灯没有回执。
- `恢复日程` 的灯态推断改为优先判断当前是否处于日程窗口，降低非照明时段误开灯风险。
- 模式文案从“行程中”统一为“日程中”。

### `coordinator.py`

- 云端临时连接失败时保留旧数据，但写入 `_stale`、`_connection_failures`、`_last_update_error`。
- 控制命令统一经过 `_async_control`，把认证、连接、协议错误转换为 HA 可读错误。
- 控制记录增加期望地址、已回执地址、缺失地址。

### `entity.py`

- 实体 `available` 增加 `_stale` 判断，云端短暂失败时实体会显示不可用，而不是继续用旧状态装作正常。
- 设备注册 `identifiers` 未改；设备名 fallback 从 `Netlea Aquarium` 改为 `尼特利鱼缸`。

### `light.py`

- 主灯增加图标 `mdi:led-strip-variant`。
- 属性里增加 `expected_addresses`、`missing_addresses`、`stale`、`last_update_error`。

### `button.py`

- 临时开灯图标改为 `mdi:lightbulb-on-outline`。
- 临时关灯图标改为 `mdi:lightbulb-off-outline`。
- 恢复日程图标改为 `mdi:calendar-sync-outline`。

### `sensor.py`

- 当前日程图标改为 `mdi:calendar-clock-outline`。
- 控制回执图标改为 `mdi:check-network-outline`。
- 控制回执、在线灯数、最后操作标为 `EntityCategory.DIAGNOSTIC`。

### `binary_sensor.py`

- 已配置日程图标改为 `mdi:calendar-check-outline`。
- 日程运行中图标改为 `mdi:calendar-play-outline`。

### `config_flow.py`

- 增加设备稳定 key 清洗，过滤缺少 `equipmentUserId`、`controlRealAddress`、`realAddress` 的设备，避免生成 `"None"` 类 unique id。
- 重新登录时校验新账号仍能看到原设备，再更新 token/user_id/control address。
- 默认 entry title 和设备名 fallback 改为 `尼特利鱼缸`。

### `manifest.json`

- 集成显示名改为 `尼特利鱼缸`。
- 增加 `integration_type: device`。

### `strings.json`

- 英文实体名调整为更清晰的命名，例如 `Main Light`、`Light Mode`、`Current Schedule`、`Control Replies`、`Last Action`。
- 补充 `device_not_found` abort 文案。

### `translations\zh-Hans.json`

- 中文配置流标题改为 `尼特利鱼缸`。
- 实体名统一为 `主灯`、`恢复日程`、`灯光模式`、`当前日程`、`控制回执`、`最后操作`、`已配置日程`、`日程运行中`。
- 补充 `device_not_found` abort 文案。

### `CODEX_DONE.md`

- 新增本收尾记录。

## 3. 修复的问题

- 修复控制命令“WebSocket 连接成功但设备无回执仍被当作成功”的风险。
- 修复 WebSocket 关闭/错误帧没有立即失败、可能等到超时的问题。
- 修复 HTTP `429/5xx`、云端临时错误没有参与重试的问题。
- 修复字符串型状态字段可能被误判的问题，例如 `"0"` 被当作真值。
- 降低“恢复日程”在非日程窗口误开灯的风险。
- 修复云端短暂失败时 HA 实体继续显示可用、自动化可能误用旧状态的问题。
- 修复无稳定设备 key 时可能产生 `"None"` unique id 的风险。
- 改善重新登录流程，避免换账号后直接覆盖原 entry token。
- 统一实体命名和图标，让 HA 里更容易识别。

## 4. 未解决风险

- 没有连接 HA、没有连接尼特利云、没有实机控制鱼缸灯；协议帧和回执语义仍需生产前实测确认。
- `dayList` 的星期顺序仍按 Python `weekday()` 处理，即周一为 0；如果尼特利云实际以周日为 0，需要再调整。
- 已存在于 HA entity registry 的实体，如果用户曾手动改名，HA 可能保留用户自定义名称，不一定自动使用新翻译名。
- MDI 图标名称需要在目标 HA 前端确认显示效果；若当前 HA 前端图标库版本较旧，个别 outline 图标可能 fallback。
- `manifest.json` 的中文 `name` 会影响集成显示名，需要确认 HACS/HA 自定义集成列表展示是否符合预期。

## 5. 对关键 HA 元数据/命名逻辑的影响

- `manifest.json`：有改动，`name` 改为 `尼特利鱼缸`，新增 `integration_type: device`。
- `translations\zh-Hans.json`：有改动，统一中文配置流和实体显示名。
- `strings.json`：有改动，统一英文实体显示名，补充 abort 文案。
- `config_flow.py`：有改动，新增稳定 key 过滤、reauth 原设备校验、默认名称调整。
- 实体 `unique_id` 格式：没有改，仍由 `entity.py` 使用 `f"{coordinator.device_key}_{suffix}"`；但 `config_flow.py` 现在会阻止无稳定 key 的设备进入配置，降低 unique id 异常风险。
- 设备注册逻辑：`DeviceInfo.identifiers` 没有改；设备名 fallback 改为 `尼特利鱼缸`；实体可用性增加 `_stale` 判断，不改变设备注册标识。

## 6. 已运行检查

### JSON 解析检查

命令：

```powershell
Get-Content -LiteralPath 'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium\manifest.json' -Raw | ConvertFrom-Json | Out-Null; Get-Content -LiteralPath 'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium\strings.json' -Raw | ConvertFrom-Json | Out-Null; Get-Content -LiteralPath 'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium\translations\zh-Hans.json' -Raw | ConvertFrom-Json | Out-Null; 'JSON parse OK'
```

结果：

```text
JSON parse OK
```

### Python 语法检查

命令：

```powershell
@'
import ast
from pathlib import Path
root = Path(r'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium')
for path in sorted(root.rglob('*.py')):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('Python syntax OK')
'@ | python -B -
```

结果：

```text
Python syntax OK
```

### 生成文件和临时文件检查

命令：

```powershell
$root = 'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium'; $items = Get-ChildItem -LiteralPath $root -Recurse -Force | Where-Object { $_.Name -eq '__pycache__' -or $_.Name -like '*.pyc' -or $_.Name -like '*.pyo' -or $_.Name -like '*.bak' -or $_.Name -like '*.tmp' -or $_.Name -like '*~' -or $_.Name -like '*.orig' -or $_.Name -like '*.rej' }; if ($items) { $items | Select-Object -ExpandProperty FullName; exit 1 } else { 'No generated or temporary files' }
```

结果：

```text
No generated or temporary files
```

### 敏感信息检查

命令：

```powershell
$root = 'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium'; $patterns = @('BEGIN .*PRIVATE KEY','password\s*[:=]','passwd\s*[:=]','secret\s*[:=]','api[_-]?key\s*[:=]'); $hits = @(); foreach ($pattern in $patterns) { $out = rg -n --glob '!CODEX_DONE.md' $pattern $root; if ($LASTEXITCODE -eq 0) { $hits += $out } elseif ($LASTEXITCODE -gt 1) { exit $LASTEXITCODE } }; if ($hits.Count -gt 0) { $hits; exit 1 } else { 'No hardcoded password/secret/private-key patterns found' }
```

结果：

```text
No hardcoded password/secret/private-key patterns found
```

补充检查：

```powershell
rg -n --glob '!CODEX_DONE.md' "token|Authorization|CONF_TOKEN|CONF_PHONE|CONF_UUID|CONF_USER_ID" "D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium"
```

结果说明：

- 命中均为配置字段名、运行时变量、运行时 header 构造逻辑。
- 未发现真实 token、手机号、密码、私钥正文或固定凭据。

最终包含 `CODEX_DONE.md` 在内的疑似凭据字面值检查：

```powershell
@'
from pathlib import Path
import re
root = Path(r'D:\UserData\Desktop\codex\Ha\ha_custom_components_rewrite\work\custom_components\netlea_aquarium')
patterns = [
    re.compile(r'BEGIN [A-Z ]*PRIVATE KEY'),
    re.compile(r'(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*["\']?[A-Za-z0-9._~+/=-]{12,}'),
]
hits = []
for path in sorted(root.rglob('*')):
    if not path.is_file():
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        continue
    for lineno, line in enumerate(text.splitlines(), 1):
        if any(pattern.search(line) for pattern in patterns):
            hits.append(f'{path}:{lineno}:{line}')
if hits:
    print('\n'.join(hits))
    raise SystemExit(1)
print('No credential-like literals found')
'@ | python -B -
```

结果：

```text
No credential-like literals found
```

### HA 生产操作确认

- 未修改 HA 生产配置目录。
- 未连接 HA API。
- 未 SSH 到 HAOS。
- 未执行 HA reload/restart。
- 未修改其他自定义插件。

## 7. 替换到 HA 生产环境前重点验证

- 配置流能否发送验证码、登录、列出正确设备组。
- 已有 entry 重新认证时，是否能正确匹配原设备，不会误绑定到其他设备。
- 主灯实体显示名是否为 `主灯`，设备名/集成名是否显示为预期的 `尼特利鱼缸`。
- 临时开灯、临时关灯、恢复日程三个动作是否都能收到设备回执；重点看 `expected_addresses`、`replied_addresses`、`missing_addresses`。
- 云端断网或尼特利接口短暂异常时，实体是否进入不可用，而不是继续显示旧状态为正常。
- 当前日程、灯光模式、日程运行中是否和尼特利 App 当前状态一致。
- 非照明日程窗口内执行 `恢复日程`，确认不会误开灯。
- 跨午夜日程和一周不同日期日程，确认 `dayList` 星期顺序正确。
- HA 前端图标是否全部正常显示。
- 现有自动化若依赖旧实体显示名或旧属性文案，需要确认是否受影响；实体 `unique_id` 格式本轮没有改变。
