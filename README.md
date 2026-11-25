# 南桥材料试验厂双工位输送线盘点

质检班用双向输送线移交待检测件。接驳员需要查实物的当前位置。班长在输送继续时签交接盘点，次班用固定清单决定去站内找件还是沿线按趟找件。

## 运行

使用 Python 3.11 标准库。项目根目录运行 `python3 -B check_project.py` 执行完整正常回归。控制台物化工程时提供该入口。`python3 -B demo.py` 从 `samples/notice_first.json` 重放正常班次，在临时工作库签出 18 g 的盘点，办理后续到达后关闭录制源并重开工作库，复领原首执及途中出处。`samples/round_trip.json` 提供同件两个方向的独立趟次。两个样例都保留站点原序和原设备记录标识。

## 班次和实物

1. 用 `storage.connect(path, window=None)` 打开工作库。首次打开创建表并保存每站源窗口大小，省略时为 64 条。重开沿用原窗口。连接由调用方关闭。
2. `intake.shifts.open_shift(db, shift_id, reference, remote)` 建立班期。`intake.registration.register(db, shift_id, item_id, station, grams, origin)` 登记班初件。`intake.opening_stock.initialize_opening_stock` 接受同样的班号和站点以及一批含 `item_id, station, grams, origin` 的登记，整批成功或回滚。相同班期及登记可重放。已有动作或盘点请求后，班初计量集合固定，重复初始化继续沿用原称重及已经发生的转运。
3. `movement.transfer.move(db, action, before_arrival=None)` 接受 `StationAction` 或同字段字典。放行动作为 `departure`，点收为 `arrival`。每站动作原序自 1 连续增长。`origin` 是原动作出处，`record_key` 是该动作在设备记录中的原始标识。到达另外引用同趟的 `departure_sequence` 和 `departure_origin`。相同动作重投可复用，趟号、原序、放行绑定、站点或实物冲突抛 `ConflictError` 并保持当前状态。`movement.live_location.locate(db, shift_id, item_id)` 返回驻留站或途中及原据，未知件返回 `None`。趟号在班内唯一，返程另用新趟号。

## 不停线盘点

1. `capture.requests.count_request(db, count_id, shift_id, reference, remote)` 用盘点号建立请求。相同参数重用，异参拒绝。先调用 `capture.markers.record_start(db, count_id, remote)`，再调用 `capture.station_evidence.capture_reference(db, count_id)` 固定并保全甲截点。
2. 录制回放使用 `movement.recording.LocalRecording(db, shift_id, steps, before_arrival=protect_reference_cuts)`，其中 `protect_reference_cuts` 来自 `capture.station_evidence`。在线使用 `move` 时传同一观察函数。观察发生在点收校验通过后、本地动作入账前，与动作处于同一事务。`advance()` 每次办理一条动作，耗尽返回 `None`。单次失败可以重试同一录制动作。
3. 也可用 `preserve_station_evidence(db, count_id, remote, position=...)` 保存已经取得的真本地乙候选。`capture_notice(db, count_id)` 固定通知水位，沿用已保存候选，首次取证时使用当前边界。乙候选允许落在起始与通知之间，含两个边界。重复调用沿用已经固定的位置。保存的本地前缀可从源窗口中取得，也可按原号分批交付。`deferred` 列出本次未交付原件的序位，位置和前缀摘要仍随本次取证保存。用 `merge_trip_evidence(db, count_id, actions)` 补交在源侧收到的原记录。已经滚掉且未保全的原件须由上游补交，不足以证明时盘点待齐。
4. `inventory.issue.issue(db, count_id)` 返回 `issued`、`pending` 或 `rejected`。正式件包含甲乙两端原序边界、证据摘要、逐件清单、原整数克重和首执。截面内到达必须有对端截面内的同趟放行。放行已纳入、到达未纳入的实物列途中，保存原放行和称重。空类记零。克重使用 Python 整数求和，拒绝缺值、布尔值和非整数。`pending` 和 `rejected` 均无新增正式记录和首执。正式写入遇到 SQLite 错误时回滚并传播错误。正式号再次办理返回原成果和首执。

低层标记入口 `fix_reference_cut` 和 `record_notice` 可分别采录实际标记。独立采集时须同时保全对应位置证明，`resume_capture` 按原号读回已保存材料。盘点前缀的摘要来自采录时的站点原序及原始动作，用于核对分批材料是否复原该前缀；录制和归档使用同一受控本地工作库。

## 关源交接

`LocalRecording.read_available(station, first=1, last=None)` 只读源窗口，`close_source()` 关闭该录制端口。现场推进会逐站滚掉较早原件。`history.retrieve.retrieve(db, count_id)` 返回固定成果和首执的二元组。`history.shift_index.list_shift_reports(db, shift_id)` 按盘点号排序列本班已签盘点。`history.provenance.trace(db, count_id, item_id)` 查原称重和固定趟据。`history.reconcile.reconcile_item` 同时给出固定边界、归属依据和现场追查出处。未知或待签号、以及不存在的盘点条目返回 `None`。
