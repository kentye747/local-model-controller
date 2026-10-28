import SwiftUI
import AppKit
import ApplicationServices
import UniformTypeIdentifiers

struct ConversationTarget: Decodable { let kind, agent, sessionId, title, url: String }
struct ConversationResult: Decodable { let target: ConversationTarget }

@MainActor enum ConversationNavigator {
    static func failure(_ text:String) -> NSError { NSError(domain:"Conversation",code:1,userInfo:[NSLocalizedDescriptionKey:text]) }
    static func attribute(_ e:AXUIElement,_ name:String) -> CFTypeRef? {
        var v:CFTypeRef?;guard AXUIElementCopyAttributeValue(e,name as CFString,&v) == .success else{return nil};return v
    }
    static func text(_ e:AXUIElement,_ name:String) -> String { attribute(e,name) as? String ?? "" }
    static func children(_ e:AXUIElement) -> [AXUIElement] { attribute(e,kAXChildrenAttribute) as? [AXUIElement] ?? [] }
    static func find(_ root:AXUIElement,stopAfter:Int=3000,where predicate:(AXUIElement)->Bool) -> [AXUIElement] {
        var queue=[root],out:[AXUIElement]=[],i=0
        while i<queue.count && i<3000 {
            let e=queue[i];i+=1
            if predicate(e) {out.append(e);if out.count>=stopAfter {break}}
            // No need to inspect conversation transcripts while navigating the sidebar.
            if text(e,kAXRoleAttribute) != kAXStaticTextRole {queue.append(contentsOf:children(e))}
        }
        return out
    }
    static func open(_ target:ConversationTarget) async throws {
        guard let url=URL(string:target.url),["hermes","dsh"].contains(url.scheme ?? "") else{throw failure(L("对话入口无效，请刷新。"))}
        if target.kind == "url" {
            guard NSWorkspace.shared.open(url) else{throw failure(L("未能打开 Hermes，请确认应用已安装。"))};return
        }
        guard target.kind == "dsh_accessibility" else{throw failure(L("不支持这个会话入口。"))}
        guard AXIsProcessTrusted() else {
            let alert=NSAlert();alert.messageText=L("允许控制器定位 DSH 对话")
            alert.informativeText=L("当前 DSH 只支持打开软件，没有指定会话的链接。控制器需要“辅助功能”权限，才能在 DSH 的任务列表中查找并打开这一条对话。不会发送消息或修改对话内容。\n\n请在系统设置中为“本地模型控制器”开启辅助功能，再点击任务名。")
            alert.addButton(withTitle:L("打开系统设置"));alert.addButton(withTitle:L("暂不授权"))
            if alert.runModal() == .alertFirstButtonReturn {
                NSWorkspace.shared.open(URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!)
            }
            throw failure(L("DSH 会话定位尚缺辅助功能授权；Hermes 会话链接可直接使用。"))
        }
        guard NSWorkspace.shared.open(url) else{throw failure(L("未能打开 DSH，请确认应用已安装。"))}
        var app:NSRunningApplication?
        for _ in 0..<60 {
            app=NSRunningApplication.runningApplications(withBundleIdentifier:"com.deepseek.dsh").first
            if app != nil {break};try await Task.sleep(nanoseconds:100_000_000)
        }
        guard let app=app else{throw failure(L("DSH 还在启动，请稍候再点击任务名。"))}
        app.activate(options:.activateIgnoringOtherApps)
        let ax=AXUIElementCreateApplication(app.processIdentifier)
        var search:AXUIElement?
        for _ in 0..<40 {
            search=find(ax,stopAfter:1) { e in
                let role=text(e,kAXRoleAttribute)
                return (role==kAXTextFieldRole || role=="AXSearchField") &&
                    [kAXDescriptionAttribute,kAXPlaceholderValueAttribute,kAXTitleAttribute].contains { key in
                        let label=text(e,key);return label.contains("搜索会话名称") || label.contains("Search session")
                    }
            }.first
            if search != nil {break};try await Task.sleep(nanoseconds:100_000_000)
        }
        guard let search=search else{throw failure(L("未找到 DSH 会话搜索框，请打开 DSH 主窗口后重试。"))}
        let previous=text(search,kAXValueAttribute)
        defer {AXUIElementSetAttributeValue(search,kAXValueAttribute as CFString,previous as CFString)}
        guard AXUIElementSetAttributeValue(search,kAXValueAttribute as CFString,target.title as CFString) == .success else{throw failure(L("无法填写 DSH 会话搜索框。"))}
        var targetRow:AXUIElement?
        for _ in 0..<30 {
            let outline=find(ax,stopAfter:1) {text($0,kAXRoleAttribute)==kAXOutlineRole}.first
            guard let outline=outline else{throw failure(L("DSH 会话列表尚未就绪，请稍后重试。"))}
            let rows=find(outline) {e in
                guard text(e,kAXRoleAttribute)==kAXRowRole else{return false}
                // Require an exact text node, not a substring of another task title.
                return find(e) {text($0,kAXRoleAttribute)==kAXStaticTextRole}.contains { item in
                    var value=text(item,kAXValueAttribute)
                    for prefix in ["进行中 ","Running "] where value.hasPrefix(prefix) {value=String(value.dropFirst(prefix.count))}
                    return value==target.title || value.hasPrefix(target.title+" ")
                }
            }
            if rows.count==1 {targetRow=rows[0];break}
            if rows.count>1 {throw failure(L("DSH 搜索结果存在同名任务，已停止自动定位。"))}
            try await Task.sleep(nanoseconds:100_000_000)
        }
        guard let row=targetRow else{throw failure(L("未找到对应的 DSH 对话；请确认它未被归档或改名。"))}
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier==app.processIdentifier else{throw failure(L("前台窗口已变化，本次没有点击其他应用。"))}
        if AXUIElementPerformAction(row,kAXPressAction as CFString) != .success {
            guard let p=attribute(row,kAXPositionAttribute),let s=attribute(row,kAXSizeAttribute),CFGetTypeID(p)==AXValueGetTypeID(),CFGetTypeID(s)==AXValueGetTypeID() else{throw failure(L("DSH 未提供可打开的任务行。"))}
            var point=CGPoint.zero,size=CGSize.zero
            AXValueGetValue(unsafeBitCast(p,to:AXValue.self),.cgPoint,&point)
            AXValueGetValue(unsafeBitCast(s,to:AXValue.self),.cgSize,&size)
            guard size.width>0 && size.height>0 else{throw failure(L("对应任务当前不可见，请在 DSH 中重试。"))}
            let center=CGPoint(x:point.x+size.width/2,y:point.y+size.height/2)
            CGEvent(mouseEventSource:nil,mouseType:.leftMouseDown,mouseCursorPosition:center,mouseButton:.left)?.post(tap:.cghidEventTap)
            CGEvent(mouseEventSource:nil,mouseType:.leftMouseUp,mouseCursorPosition:center,mouseButton:.left)?.post(tap:.cghidEventTap)
        }
        for _ in 0..<30 {
            let windows=attribute(ax,kAXWindowsAttribute) as? [AXUIElement] ?? []
            if windows.contains(where:{text($0,kAXTitleAttribute)==target.title+" — DeepSeek Harness"}) {return}
            try await Task.sleep(nanoseconds:100_000_000)
        }
        throw failure(L("已请求定位，但尚未确认 DSH 打开了目标对话，请在软件中核对。"))
    }
}

struct ModelActivity: Decodable, Identifiable {
    let agent, sessionId, title, phase, source, confidence: String
    let host: String?
    let canOpen: Bool?
    var id: String { agent+":"+sessionId }
}
struct LocalModel: Decodable, Identifiable {
    let id, name, engine, modelId, baseUrl: String
    let context: Int
    let estimatedBytes: Double
    let vision: Bool
    let state, stateText: String
    let memoryBytes: Double?
    let memoryKind: String
    let busy: Bool?
    let canLoad, canRelease: Bool
    let detail, error: String
    let inputTps, outputTps: Double?
    let speedLabel: String
    let activities: [ModelActivity]
    let activityState, activityNote: String
    var color: Color { state == "ready" ? (busy == true ? .orange : .green) : state == "loading" ? .orange : state == "foreign" || state == "unknown" ? .red : .secondary }
}
struct AgentRoute: Decodable, Identifiable {
    let id, name, selection, current: String
    let configured: Bool
    let note: String
    let running, canRestore: Bool
}
struct MemoryInfo: Decodable {
    let total, used, available, percent, wired, compressed, swap: Double
    let pressure: String
}
struct Preferences: Decodable { let memoryLimit: Int; let evictIdle, monitorSpeeds: Bool }
struct Dashboard: Decodable {
    let models: [LocalModel]
    let agents: [AgentRoute]
    let memory: MemoryInfo
    let preferences: Preferences
    let updated: String
    let historyNote:String?
}
func gib(_ bytes: Double) -> String { String(format:"%.1f GiB", bytes / 1_073_741_824) }

@MainActor final class Controller: ObservableObject {
    @Published var dashboard: Dashboard?
    @Published var operation: String?
    @Published var message = ""
    @Published var error = ""
    @Published var tab = 0
    @Published var choices: [String:String] = [:]
    @Published var limit = 90
    @Published var evict = false
    @Published var monitor = true
    @Published var statusFresh = false
    @Published var historyReport:UsageHistoryReport?
    @Published var historyModel="all"
    @Published var historyDays=0
    @Published var historyOffset=0
    @Published var historyLoading=false
    @Published var historyError=""
    @Published var motionEnabled=UserDefaults.standard.object(forKey:"lightweightMotion") as? Bool ?? true {
        didSet {UserDefaults.standard.set(motionEnabled,forKey:"lightweightMotion")}
    }
    @Published var expandedModels:Set<String>=[]
    @Published var showInteractionPreview=false
    @Published private(set) var operationKind:String?
    @Published private(set) var operationTarget:String?
    @Published private(set) var manualRefreshing=false
    private var initialized = false
    private var refreshing = false
    private var telemetry: Process?
    static func decoder() -> JSONDecoder { let d=JSONDecoder();d.keyDecodingStrategy = .convertFromSnakeCase;return d }
    nonisolated static func backendCommand(_ args:[String]) throws -> (URL,[String]) {
        let backend=Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/Backend/LMCBackend")
        if FileManager.default.isExecutableFile(atPath:backend.path) {return (backend,args)}
        let environment=ProcessInfo.processInfo.environment
        let candidates=[environment["LMC_PYTHON"],Bundle.main.resourceURL?.appendingPathComponent("python-path").path]
            .compactMap{$0}.filter{FileManager.default.isExecutableFile(atPath:$0)}
        guard let python=candidates.first else {
            throw NSError(domain:"Controller",code:1,userInfo:[NSLocalizedDescriptionKey:L("缺少控制器运行环境，请重新安装完整应用。")])
        }
        return (URL(fileURLWithPath:python),["-B",Bundle.main.resourceURL!.appendingPathComponent("local_controller.py").path]+args)
    }
    nonisolated static func bridge(_ args: [String]) throws -> Data {
        let p=Process();let command=try backendCommand(args);p.executableURL=command.0;p.arguments=command.1
        let pipe=Pipe();p.standardOutput=pipe;p.standardError=FileHandle.nullDevice
        try p.run()
        let data=pipe.fileHandleForReading.readDataToEndOfFile();p.waitUntilExit()
        if p.terminationStatus != 0 {
            let info=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any]
            throw NSError(domain:"Controller",code:2,userInfo:[NSLocalizedDescriptionKey:info?["error"] as? String ?? L("本机控制接口暂不可用，请刷新重试。")])
        }
        return data
    }
    func refresh() async {
        guard !refreshing else { return }
        refreshing=true;defer{refreshing=false}
        do {
            let bytes=try await Task.detached { try Self.bridge(["status"]) }.value
            let next=try Self.decoder().decode(Dashboard.self,from:bytes)
            dashboard=next
            statusFresh=true
            syncTelemetry(enabled:next.preferences.monitorSpeeds)
            for a in next.agents where choices[a.id] == nil { choices[a.id]=a.selection }
            if !initialized { limit=next.preferences.memoryLimit;evict=next.preferences.evictIdle;monitor=next.preferences.monitorSpeeds;initialized=true }
            if tab==3 {await loadHistory()}
        } catch { statusFresh=false;self.error=error.localizedDescription }
    }
    func loadHistory() async {
        guard !historyLoading else{return}
        historyLoading=true;historyError=""
        let model=historyModel,days=historyDays,offset=historyOffset
        do {
            let data=try await Task.detached {try Self.bridge(["history",model,String(days),String(offset)])}.value
            let report=try Self.decoder().decode(UsageHistoryReport.self,from:data)
            if historyModel==model && historyDays==days && historyOffset==offset {historyReport=report}
        } catch {historyError=L("历史统计暂不可读，请刷新重试。")}
        historyLoading=false
        if historyModel != model || historyDays != days || historyOffset != offset {await loadHistory()}
    }
    func refreshManually() {
        guard !manualRefreshing else{return}
        manualRefreshing=true
        Task {
            if refreshing {while refreshing {try? await Task.sleep(nanoseconds:100_000_000)}}
            else {await refresh()}
            manualRefreshing=false
        }
    }
    func isPerforming(_ kind:String,_ target:String)->Bool {operation != nil && operationKind==kind && operationTarget==target}
    func exportHistory() {
        guard operation==nil else{return}
        let panel=NSSavePanel();panel.title=L("导出模型使用历史");panel.allowedContentTypes=[.commaSeparatedText]
        panel.nameFieldStringValue=L("模型使用历史.csv");panel.canCreateDirectories=true
        guard panel.runModal() == .OK,let url=panel.url else{return}
        perform(L("导出使用历史"),["history-export",historyModel,String(historyDays),url.path])
    }
    private func syncTelemetry(enabled:Bool) {
        if !enabled {
            if telemetry?.isRunning == true { telemetry?.terminate() }
            telemetry=nil;return
        }
        guard telemetry?.isRunning != true else { return }
        guard let command=try? Self.backendCommand(["--watch-lm",String(ProcessInfo.processInfo.processIdentifier)]) else{return}
        let p=Process();p.executableURL=command.0;p.arguments=command.1
        p.standardOutput=FileHandle.nullDevice;p.standardError=FileHandle.nullDevice
        do { try p.run();telemetry=p } catch { telemetry=nil }
    }
    func perform(_ label: String, _ args: [String]) {
        guard operation == nil else { return }
        operation=label;message="";error=""
        operationKind=args.first;operationTarget=args.count>1 ? args[1] : nil
        Task {
            do {
                let bytes=try await Task.detached { try Self.bridge(args) }.value
                let result=(try JSONSerialization.jsonObject(with:bytes)) as? [String:Any]
                message=result?["message"] as? String ?? L("操作已完成。")
                if let link=result?["open_url"] as? String, let url=URL(string:link) { NSWorkspace.shared.open(url) }
                if args.first == "restore" { choices.removeValue(forKey:args[1]) }
            } catch { self.error=error.localizedDescription }
            operation=nil
            operationKind=nil;operationTarget=nil
            await refresh()
        }
    }
    func release(_ model: LocalModel) {
        if model.busy == nil {
            let alert=NSAlert()
            alert.messageText=L("确认 \(model.name) 没有正在执行的任务？")
            alert.informativeText=L("LM Studio 没有提供可靠的请求状态。释放会停止使用它的任务；确认空闲后再释放。")
            alert.addButton(withTitle:L("确认释放"));alert.addButton(withTitle:L("取消"))
            if alert.runModal() != .alertFirstButtonReturn { return }
            perform(L("释放 \(model.name)"),["release",model.id,"--confirm-unknown"])
        } else { perform(L("释放 \(model.name)"),["release",model.id]) }
    }
    func openConversation(_ activity:ModelActivity) {
        guard operation == nil else{return}
        operation=L("打开 \(activity.agent) 对话");error="";message=""
        Task {
            do {
                let data=try await Task.detached {try Self.bridge(["conversation",activity.agent,activity.sessionId])}.value
                let result=try Self.decoder().decode(ConversationResult.self,from:data)
                try await ConversationNavigator.open(result.target)
                message=L("已打开 \(activity.agent) 中的对应对话。")
            } catch {self.error=error.localizedDescription}
            operation=nil
        }
    }
}

struct MainView: View {
    @StateObject private var c=Controller()
    @AppStorage("controllerLanguage") private var language="system"
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var working: Bool { c.operation != nil }
    private var pageMotion:Animation? {c.motionEnabled && !reduceMotion ? .easeOut(duration:0.16) : nil}
    private var detailMotion:Animation? {c.motionEnabled && !reduceMotion ? .easeOut(duration:0.22) : nil}
    var body: some View {
        VStack(alignment:.leading,spacing:0) {
            HStack(spacing:12) {
                Image(systemName:"cpu").font(.system(size:28,weight:.medium)).foregroundStyle(Color.accentColor).accessibilityHidden(true)
                VStack(alignment:.leading,spacing:3) {
                    Text(L("本地模型控制器")).font(.system(size:23,weight:.semibold))
                    Text(L("模型、任务、内存与历史")).font(.system(size:13)).foregroundStyle(.secondary)
                }
                Spacer()
                Button {c.refreshManually()} label:{OperationButtonLabel(title:L("刷新"),symbol:"arrow.clockwise",activeTitle:L("刷新中…"),active:c.manualRefreshing)}.buttonStyle(FeedbackButtonStyle(working:c.manualRefreshing)).disabled(working || c.manualRefreshing).keyboardShortcut("r",modifiers:.command)
                Menu { Button(L("重新扫描 LM Studio 模型")) { c.perform(L("扫描本机模型"),["scan"]) }
                    Button(L("查看 TensorFold 日志")) { openPath(".local/share/tensorfold/logs/server.log") }
                    Button(L("查看 Harness 日志")) { openPath("Library/Application Support/Local Model Controller/dsh.log") }
                    Button(L("打开历史数据位置")) {openPath("Library/Application Support/Local Model Controller")}
                    Divider()
                    Picker(L("语言 / Language"),selection:$language) {
                        Text(L("跟随系统")).tag("system")
                        ForEach(UILanguage.available) {item in Text(item.name).tag(item.id)}
                    }
                    Button(L("打开连接设置")) {c.perform(L("打开连接设置"),["settings-open"])}
                    Toggle(L("轻盈动效"),isOn:$c.motionEnabled)
                    Button(L("交互预览")) {c.showInteractionPreview=true}
                } label: { Image(systemName:"ellipsis.circle") }.menuStyle(.borderlessButton).fixedSize().help(L("更多")).disabled(working)
                    .popover(isPresented:$c.showInteractionPreview,arrowEdge:.trailing) {InteractionPreview(c:c)}
            }.padding(.bottom,22)
            if let d=c.dashboard {
                if c.tab != 3 {memoryStrip(d.memory)}
                ControlNavigation(c:c).padding(.vertical,18)
                ScrollView {
                    VStack(alignment:.leading,spacing:0) {
                        if c.tab==0 { modelList(d) }
                        else if c.tab==1 { agentList(d) }
                        else if c.tab==2 { memorySettings(d) }
                        else {HistoryView(c:c)}
                    }.padding(.bottom,10).id(c.tab).transition(.opacity)
                        .animation(pageMotion,value:c.tab)
                }
            } else {
                Spacer();HStack { Spacer();ProgressView(L("正在读取本机模型与 Agent 配置…"));Spacer() };Spacer()
            }
            feedback
            Divider().padding(.bottom,12)
            HStack {
                Text(L("关闭窗口不会释放模型；模型控制作用于这台 Mac，mini 调用只读展示。")).font(.system(size:11)).foregroundStyle(.secondary)
                Spacer()
                if let d=c.dashboard { Text(L("更新于 ")+d.updated).font(.system(size:11)).monospacedDigit().foregroundStyle(.secondary) }
            }
        }
        .padding(26)
        .frame(minWidth:850,idealWidth:980,maxWidth:1400,minHeight:760,idealHeight:980,maxHeight:1300)
        .background(Color(nsColor:.windowBackgroundColor))
        .buttonStyle(FeedbackButtonStyle())
        .environment(\.controllerMotion,c.motionEnabled)
        .onChange(of:language) {_ in c.message="";c.error="";c.refreshManually()}
        .environment(\.locale,Locale(identifier:UILanguage.resolved))
        .onChange(of:c.tab) {value in if value==3 {Task{await c.loadHistory()}}}
        .task { while !Task.isCancelled { await c.refresh();try? await Task.sleep(nanoseconds:4_000_000_000) } }
    }
    private func openPath(_ relative:String) { NSWorkspace.shared.open(FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(relative)) }
    @ViewBuilder private var feedback:some View {
        VStack(alignment:.leading,spacing:8) {
            if let action=c.operation {
                HStack(spacing:9) {ProgressView().controlSize(.small);Text(L(action)+"…");Spacer()}.font(.system(size:12)).padding(12).background(Color.accentColor.opacity(0.06),in:RoundedRectangle(cornerRadius:10)).transition(.opacity)
            }
            if !c.error.isEmpty || !c.message.isEmpty {
                HStack(alignment:.top,spacing:9) {
                    Image(systemName:c.error.isEmpty ? "checkmark.circle.fill" : "exclamationmark.triangle.fill").foregroundStyle(c.error.isEmpty ? Color.green : .red)
                    Text(L(c.error.isEmpty ? c.message : c.error)).textSelection(.enabled).fixedSize(horizontal:false,vertical:true)
                    Spacer(minLength:0)
                    Button {c.message="";c.error=""} label:{Image(systemName:"xmark")}.buttonStyle(FeedbackButtonStyle(emphasis:.quiet)).accessibilityLabel(L("收起操作提示"))
                }.font(.system(size:12)).padding(12).background(Color.primary.opacity(0.035),in:RoundedRectangle(cornerRadius:10)).transition(.opacity)
            }
        }.padding(.vertical,c.operation != nil || !c.message.isEmpty || !c.error.isEmpty ? 10 : 0)
            .animation(pageMotion,value:c.operation).animation(pageMotion,value:c.message).animation(pageMotion,value:c.error)
    }
    private func memoryStrip(_ m: MemoryInfo) -> some View {
        VStack(alignment:.leading,spacing:10) {
            HStack(alignment:.firstTextBaseline) {
                Text(L("整机内存")).font(.system(size:14,weight:.medium))
                Text("\(gib(m.used)) / \(gib(m.total))").font(.system(size:13)).monospacedDigit().foregroundStyle(.secondary)
                Spacer()
                Text(String(format:"%.0f%%",m.percent)).font(.system(size:19,weight:.semibold)).monospacedDigit()
            }
            ProgressView(value:min(100,max(0,m.percent)),total:100).tint(m.pressure=="紧张" ? .red : m.pressure=="偏高" ? .orange : .accentColor).accessibilityLabel(L("整机内存使用率"))
            HStack(spacing:18) {
                Text(L("压力：")+L(m.pressure))
                Text(L("交换内存：")+gib(m.swap))
                Spacer()
                Text(L("可用估算：")+gib(m.available))
            }.font(.system(size:12)).foregroundStyle(.secondary).monospacedDigit()
        }
    }
    private func modelList(_ d:Dashboard) -> some View {
        VStack(alignment:.leading,spacing:10) {
            HStack {
                Text(L("本机模型")).font(.system(size:15,weight:.semibold))
                Spacer()
                Button(L("释放已确认空闲的模型")) { c.perform(L("释放空闲模型"),["release_idle"]) }
                    .disabled(working || !d.models.contains(where:{$0.state=="ready" && $0.busy==false}))
            }.padding(.bottom,8)
            if d.models.isEmpty {
                VStack(alignment:.leading,spacing:10) {
                    Label(L("还没有发现本地模型"),systemImage:"cpu")
                    Text(L("启动 LM Studio 并下载模型后，从更多菜单扫描；也可在连接设置中启用 Hermes、llama.cpp 或 TensorFold。"))
                        .font(.system(size:12)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
                    Button(L("扫描 LM Studio 模型")) {c.perform(L("扫描本机模型"),["scan"])}.disabled(working)
                }.padding(20)
            }
            ForEach(d.models) { m in
                VStack(alignment:.leading,spacing:13) {
                    HStack(alignment:.top,spacing:16) {
                        Image(systemName: m.engine=="TensorFold" ? "cpu" : m.id.hasPrefix("hermes:") ? "bolt" : "square.stack.3d.up")
                            .font(.system(size:20,weight:.regular)).foregroundStyle(.secondary).frame(width:28,height:36).accessibilityHidden(true)
                        VStack(alignment:.leading,spacing:5) {
                            Text(m.name).font(.system(size:16,weight:.semibold)).textSelection(.enabled)
                            Text(m.engine).font(.system(size:12)).foregroundStyle(.secondary)
                        }
                        Spacer(minLength:5)
                        HStack(spacing:6) { Circle().fill(m.color).frame(width:7,height:7);Text(L(m.stateText)).font(.system(size:12,weight:.medium)) }.fixedSize()
                    }
                    HStack(alignment:.top,spacing:20) {
                        VStack(alignment:.leading,spacing:4) {
                            if let bytes=m.memoryBytes { Text(gib(bytes)+" · "+L(m.memoryKind)).monospacedDigit() }
                            else if m.estimatedBytes>0 { Text(L("加载预算约 ")+gib(m.estimatedBytes)).monospacedDigit() }
                            else {Text(L("大小未知，请配置加载预算"))}
                        }.font(.system(size:12)).foregroundStyle(.secondary).frame(width:205,alignment:.leading)
                        activityView(m).frame(maxWidth:.infinity,alignment:.leading)
                        HStack(spacing:8) {
                        Button {c.perform(L("测试 \(m.name)"),["test",m.id])} label:{OperationButtonLabel(title:L("测试"),symbol:"play",activeTitle:L("测试中…"),active:c.isPerforming("test",m.id))}.buttonStyle(FeedbackButtonStyle(working:c.isPerforming("test",m.id))).disabled(working || m.state != "ready" || m.busy==true).accessibilityLabel(L("测试 ")+m.name)
                        Button {c.release(m)} label:{OperationButtonLabel(title:L("释放"),symbol:"eject",activeTitle:L("释放中…"),active:c.isPerforming("release",m.id))}.buttonStyle(FeedbackButtonStyle(working:c.isPerforming("release",m.id))).disabled(working || !m.canRelease).accessibilityLabel(L("释放 ")+m.name)
                        Button {c.perform(L("加载 \(m.name)"),["load",m.id])} label:{OperationButtonLabel(title:m.state=="resident" ? L("启动 API") : L("加载"),symbol:"power",activeTitle:L("加载中…"),active:c.isPerforming("load",m.id))}.buttonStyle(FeedbackButtonStyle(emphasis:.primary,working:c.isPerforming("load",m.id))).disabled(working || !m.canLoad).accessibilityLabel((m.state=="resident" ? L("启动 API ") : L("加载 "))+m.name)
                        }.controlSize(.regular)
                    }
                    HStack(spacing:14) {
                        Text(L("输入 ")+speed(m.inputTps))
                        Text(L("输出 ")+speed(m.outputTps))
                        Spacer()
                        Text(L(m.speedLabel)).font(.system(size:11))
                        Button {withAnimation(detailMotion) {if c.expandedModels.contains(m.id) {c.expandedModels.remove(m.id)} else {c.expandedModels.insert(m.id)}}} label:{HStack(spacing:4){Text(L("详情"));Image(systemName:"chevron.down").font(.system(size:9,weight:.semibold)).rotationEffect(.degrees(c.expandedModels.contains(m.id) ? 180 : 0))}}.buttonStyle(FeedbackButtonStyle(emphasis:.quiet)).accessibilityLabel(L("展开或收起 ")+m.name+L(" 详情")).accessibilityValue(c.expandedModels.contains(m.id) ? L("已展开") : L("已收起"))
                    }.font(.system(size:12)).monospacedDigit().foregroundStyle(.secondary)
                        .help(L("输入是提示词处理速度，输出是生成速度。TensorFold显示含任务间隔的短窗口吞吐；Hermes显示引擎累计均速；LM Studio显示最近一次的日志统计。未提供的项显示横线，不从首Token延迟推算。"))
                    if c.expandedModels.contains(m.id) {
                        VStack(alignment:.leading,spacing:7) {
                            Divider()
                            Text(L(m.detail))
                            let users=d.agents.filter{$0.selection==m.id}.map{$0.name}
                            Text(users.isEmpty ? L("尚未分配给 Agent") : L("默认用于：")+users.joined(separator:"、"))
                            Text(m.modelId).textSelection(.enabled)
                        }.font(.system(size:11)).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.leading).transition(.opacity)
                    }
                }.padding(16).background(Color.primary.opacity(0.025),in:RoundedRectangle(cornerRadius:12))
            }
            Text(L("调用信息每 4 秒随状态刷新；任务进行中不一定正在生成。无法归属的请求会明确标注。")).font(.system(size:12)).foregroundStyle(.secondary).padding(.top,14)
            Text(L("权重估算不含全部缓存。加载保护按整机内存计算；正在使用的模型会保留。")).font(.system(size:11)).foregroundStyle(.secondary).padding(.top,5)
        }
    }
    @ViewBuilder private func activityView(_ m:LocalModel) -> some View {
        if !c.statusFresh {
            Label(L("调用状态暂不可用"),systemImage:"questionmark.circle").foregroundStyle(.secondary).font(.system(size:12))
        } else if m.activityState == "unloaded" {
            Label(L("未加载 · 无当前调用"),systemImage:"moon").foregroundStyle(.secondary).font(.system(size:12))
        } else if !m.activities.isEmpty {
            VStack(alignment:.leading,spacing:10) {
                ForEach(m.activities) { a in
                    VStack(alignment:.leading,spacing:4) {
                        HStack(spacing:6) {
                            Image(systemName:a.confidence == "live" ? "waveform" : "questionmark.circle")
                                .foregroundStyle(a.confidence == "live" ? Color.accentColor : .secondary)
                            Text(a.agent).fontWeight(.semibold)
                            Text("· "+L(a.phase)).foregroundStyle(.secondary)
                        }.font(.system(size:12))
                        taskLink(a)
                    }
                }
                if !m.activityNote.isEmpty { Text(L(m.activityNote)).font(.system(size:11)).foregroundStyle(.secondary) }
            }
        } else {
            Label(L(m.activityNote),systemImage:m.activityState == "unknown" ? "questionmark.circle" : "checkmark.circle")
                .font(.system(size:12)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
        }
    }
    private func taskHelp(_ a:ModelActivity) -> String {
        if a.host == "mini" {
            if a.sessionId.hasPrefix("process-") {
                return L("已确认 mini 上的 Hermes 调用进程；尚未取得有效会话名称。\n")+a.source
            }
            let agent=a.agent.replacingOccurrences(of:" · mini",with:"")
            return L("此对话位于 mini，请在 mini 的 ")+agent+L(" 中按任务名查看。\n")+a.source+" · "+a.sessionId
        }
        return L("此任务由 Hermes CLI 运行；当前没有对应的桌面会话链接。\n")+a.source+" · "+a.sessionId
    }
    @ViewBuilder private func taskLink(_ a:ModelActivity) -> some View {
        if a.canOpen == false {
            HStack(alignment:.firstTextBaseline,spacing:5) {
                Image(systemName:"desktopcomputer").font(.system(size:11)).foregroundStyle(.secondary)
                Text(a.title).font(.system(size:13)).lineLimit(2)
            }.help(taskHelp(a))
        } else {
            localTaskLink(a)
        }
    }
    private func localTaskLink(_ a:ModelActivity) -> some View {
        Button(action:{c.openConversation(a)}) {
            HStack(alignment:.firstTextBaseline,spacing:5) {
                Text(a.title).lineLimit(2).multilineTextAlignment(.leading)
                Image(systemName:"arrow.up.right").font(.system(size:10,weight:.medium)).accessibilityHidden(true)
            }.font(.system(size:13)).foregroundStyle(Color.accentColor)
        }.buttonStyle(.plain).disabled(working || a.sessionId.isEmpty)
            .accessibilityLabel(L("打开 ")+a.agent+L(" 对话：")+a.title)
            .help(L("点击打开 ")+a.agent+L(" 中的对应对话\n")+a.source+" · "+a.sessionId)
    }
    private func agentList(_ d:Dashboard) -> some View {
        VStack(alignment:.leading,spacing:0) {
            Text(L("为每个 Agent 选择默认模型")).font(.system(size:15,weight:.semibold)).padding(.bottom,5)
            Text(L("保存分配不会自动加载或释放模型；已有会话可能保留自己的选择。")).font(.system(size:12)).foregroundStyle(.secondary).padding(.bottom,8)
            ForEach(d.agents) { a in
                VStack(alignment:.leading,spacing:12) {
                    HStack {
                        Text(a.name).font(.system(size:17,weight:.semibold))
                        Text(a.running ? L("运行中") : L("未打开")).font(.system(size:11)).foregroundStyle(.secondary)
                        Spacer()
                        Button(L("打开")) { c.perform(L("打开 \(a.name)"),["open",a.id]) }.disabled(working)
                        Menu { Button(L("恢复上一次默认模型")) { c.perform(L("恢复 \(a.name) 配置"),["restore",a.id]) }.disabled(!a.canRestore) } label:{ Image(systemName:"ellipsis") }.menuStyle(.borderlessButton).fixedSize().disabled(working)
                    }
                    HStack(alignment:.firstTextBaseline) {
                        Text(L("当前默认")).font(.system(size:12)).foregroundStyle(.secondary).frame(width:66,alignment:.leading)
                        Text(L(a.current)).font(.system(size:13,weight:.medium)).textSelection(.enabled)
                        if let m=d.models.first(where:{$0.id==a.selection}) { Text(m.state=="ready" ? L("可使用") : L("需要先加载")).font(.system(size:11)).foregroundStyle(m.state=="ready" ? Color.green : Color.orange) }
                        Spacer()
                    }
                    HStack(spacing:12) {
                        Text(L("选择模型")).font(.system(size:12)).foregroundStyle(.secondary).frame(width:66,alignment:.leading)
                        Picker(L("\(a.name) 模型"),selection:Binding(get:{c.choices[a.id] ?? ""},set:{c.choices[a.id]=$0})) {
                            Text(L("请选择本地模型")).tag("")
                            ForEach(d.models) { m in Text(m.name+" · "+m.engine).tag(m.id) }
                        }.labelsHidden().disabled(working)
                        Button(L("保存分配")) { if let id=c.choices[a.id] { c.perform(L("设置 \(a.name) 默认模型"),["assign",a.id,id]) } }
                            .disabled(working || (c.choices[a.id] ?? "").isEmpty || c.choices[a.id]==a.selection)
                    }
                    Text(L(a.note)).font(.system(size:11)).foregroundStyle(.secondary)
                }.padding(.vertical,20)
                Divider()
            }
        }
    }
    private func memorySettings(_ d:Dashboard) -> some View {
        VStack(alignment:.leading,spacing:19) {
            Text(L("加载保护")).font(.system(size:15,weight:.semibold))
            HStack {
                Text(L("预计加载后，内存不超过"))
                Picker(L("加载内存上限"),selection:$c.limit) { ForEach([60,70,80,85,90,95],id:\.self) { Text("\($0)%").tag($0) } }.frame(width:105).labelsHidden()
                Spacer()
            }.font(.system(size:13))
            Toggle(L("加载新模型前，先释放其他已确认空闲的模型"),isOn:$c.evict).font(.system(size:13))
            Toggle(L("显示输入、输出 Token 速度（每 4 秒读取）"),isOn:$c.monitor).font(.system(size:13))
            Text(L("正在处理请求或无法确认空闲的模型会保留。LM Studio 的释放需要单独确认。这个保护只约束本控制器发起的加载，无法限制其他应用的内存。")).font(.system(size:12)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
            Button(L("保存内存设置")) { c.perform(L("保存加载保护"),["preferences",String(c.limit),c.evict ? "true" : "false",c.monitor ? "true" : "false"]) }.disabled(working)
            Divider().padding(.vertical,5)
            Text(L("当前内存明细")).font(.system(size:15,weight:.semibold))
            memoryRow(L("已用（活跃、锁定与压缩）"),d.memory.used)
            memoryRow(L("其中：锁定内存"),d.memory.wired)
            memoryRow(L("其中：压缩内存"),d.memory.compressed)
            memoryRow(L("交换内存"),d.memory.swap)
            Text(L("系统会自动管理缓存与压缩；模型释放后，内存数据可能需要几秒更新。")).font(.system(size:12)).foregroundStyle(.secondary)
        }.padding(.top,4).disabled(working)
    }
    private func memoryRow(_ label:String,_ bytes:Double) -> some View { HStack { Text(label);Spacer();Text(gib(bytes)).monospacedDigit() }.font(.system(size:13)) }
    private func speed(_ n:Double?) -> String { n.map{String(format:"%.1f tok/s",$0)} ?? "—" }
}
final class AppDelegate: NSObject,NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification:Notification) { NSApp.activate(ignoringOtherApps:true) }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication) -> Bool { true }
}
@main struct LocalModelControl: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    init() {
        if CommandLine.arguments.contains("--check-localization") {
            precondition(L("模型管理",language:"en")=="Models")
            precondition(L("模型管理",language:"zh-Hans")=="模型管理")
            precondition(L("测试 Example",language:"en")=="Test Example")
            precondition(!UILanguage.catalog.isEmpty)
            print("PASS: bilingual localization, \(UILanguage.catalog.count) entries");exit(0)
        }
        if CommandLine.arguments.contains("--check-navigation-access") {
            print(AXIsProcessTrusted() ? "accessibility=granted" : "accessibility=missing");exit(0)
        }
        if CommandLine.arguments.contains("--check-status") {
            do {
                let data=try Controller.bridge(["status"])
                let d=try Controller.decoder().decode(Dashboard.self,from:data)
                precondition(d.agents.count==2 && d.memory.percent>=0 && d.memory.percent<=100)
                print("PASS: installed bridge and native decoding; \(d.models.count) models, \(d.agents.count) agents")
                exit(0)
            } catch { print("FAIL: \(error.localizedDescription)");exit(1) }
        }
        if CommandLine.arguments.contains("--check-history") {
            do {
                let data=try Controller.bridge(["history","all","0"])
                let r=try Controller.decoder().decode(UsageHistoryReport.self,from:data)
                precondition(r.summary.requests>=0 && r.recent.count<=80)
                print("PASS: native history decoding; \(r.models.count) models, \(r.summary.requests) recorded requests")
                exit(0)
            } catch {print("FAIL: history decoding: \(error.localizedDescription)");exit(1)}
        }
    }
    var body: some Scene {
        WindowGroup(L("本地模型控制器")) { MainView() }.defaultSize(width:980,height:980).windowResizability(.contentSize)
            .commands { CommandGroup(replacing:.newItem) {} }
    }
}
