import SwiftUI
import Charts

struct HistoryModel: Decodable, Identifiable { let id,name,engine,nativeId:String }
struct HistorySummary: Decodable {
    let requests,cacheRecords,truncated,finishKnown,unknownTime:Int
    let inputTokens,cachedTokens,outputTokens,avgInputTps,avgOutputTps,maxInputTps,maxOutputTps:Double?
    let p50Seconds,p95Seconds,avgTtftSeconds,avgQueueSeconds,avgOutputTokens,cacheRatio:Double?
    let activeSeconds:Double
    let peakSystemMemory,peakSystemSwap:Double?
}
struct HistoryDay: Decodable, Identifiable {
    let day:String
    let inputTokens,outputTokens:Double
    let requests:Int
    var id:String {day}
}
struct HistoryAgent:Decodable,Identifiable {let agent:String;let seconds:Double;var id:String{agent}}
struct HistoryRecord:Decodable,Identifiable {
    let id,modelKey,modelName,source,timeBasis:String
    let completedAt:Double?
    let observedAt:Double
    let inputTokens,cachedTokens,outputTokens,inputSeconds,outputSeconds,inputTps,outputTps,totalSeconds,queueSeconds,ttftSeconds:Double?
    let finishReason:String?
    var sortTime:Double{completedAt ?? -.infinity}
    var sortInput:Double{inputTokens ?? -1}
    var sortOutput:Double{outputTokens ?? -1}
    var sortSpeed:Double{outputTps ?? -1}
    var sortDuration:Double{totalSeconds ?? -1}
    var finishLabel:String {
        switch finishReason {
        case "stop":return L("正常结束")
        case "length":return L("长度上限")
        case "tool_calls":return L("调用工具")
        case "cancelled":return L("已取消")
        case "error":return L("错误")
        default:return L("已结束")
        }
    }
}
struct UsageHistoryReport:Decodable {
    let models:[HistoryModel]
    let summary:HistorySummary
    let daily:[HistoryDay]
    let recent:[HistoryRecord]
    let agents:[HistoryAgent]
    let model:String
    let days,offset:Int
    let startedAt,updatedAt,scannedBytes,remainingBytes,databaseBytes:Double
    let skippedRecords:Int
    let note:String
}

@MainActor final class HistoryViewState:ObservableObject {
    @Published var selection:String?
    @Published var sortOrder=[KeyPathComparator(\HistoryRecord.sortTime,order:.reverse)]
}
struct HistoryView:View {
    @ObservedObject var c:Controller
    @StateObject private var local=HistoryViewState()
    private let columns=Array(repeating:GridItem(.flexible(),spacing:12),count:4)
    private var working:Bool{c.operation != nil}
    private var selectionLoading:Bool {
        c.historyLoading && (c.historyReport == nil || c.historyReport?.model != c.historyModel || c.historyReport?.days != c.historyDays || c.historyReport?.offset != c.historyOffset)
    }
    var body:some View {
        VStack(alignment:.leading,spacing:18) {
            HStack(alignment:.firstTextBaseline) {
                VStack(alignment:.leading,spacing:4) {
                    Text(L("使用历史")).font(.system(size:21,weight:.semibold))
                    Text(L("每个模型的用量、速度和已结束请求")).font(.system(size:12)).foregroundStyle(.secondary)
                }
                Spacer()
                if selectionLoading {ProgressView().controlSize(.small)}
                Button {c.exportHistory()} label:{OperationButtonLabel(title:L("导出 CSV"),symbol:"square.and.arrow.up",activeTitle:L("导出中…"),active:c.operationKind=="history-export" && c.operation != nil)}
                    .buttonStyle(FeedbackButtonStyle(working:c.operationKind=="history-export" && c.operation != nil))
                    .disabled(working || selectionLoading || (c.historyReport?.summary.requests ?? 0)==0)
            }
            HStack(spacing:16) {
                Picker(L("模型"),selection:$c.historyModel) {
                    Text(L("全部模型")).tag("all")
                    ForEach(c.historyReport?.models ?? []) {m in Text(m.name+" · "+m.engine).tag(m.id)}
                }.frame(maxWidth:420)
                Spacer(minLength:8)
                Picker(L("范围"),selection:$c.historyDays) {
                    Text(L("今天")).tag(1);Text(L("7 天")).tag(7);Text(L("30 天")).tag(30);Text(L("全部")).tag(0)
                }.pickerStyle(.segmented).frame(width:270)
            }.disabled(working)
            if let report=c.historyReport {
                content(report)
            } else if c.historyLoading {
                ProgressView(L("正在读取本机历史统计…")).frame(maxWidth:.infinity,minHeight:220)
            } else {
                Label(L("暂无历史统计；后续请求会自动记录。"),systemImage:"clock").foregroundStyle(.secondary).frame(maxWidth:.infinity,minHeight:180)
            }
            if !c.historyError.isEmpty {Label(L(c.historyError),systemImage:"exclamationmark.triangle").foregroundStyle(.orange).font(.system(size:12))}
            if let note=c.dashboard?.historyNote,!note.isEmpty {Label(L(note),systemImage:"exclamationmark.triangle").foregroundStyle(.orange).font(.system(size:12))}
        }
        .onChange(of:c.historyModel) {_ in c.historyOffset=0;local.selection=nil;Task{await c.loadHistory()}}
        .onChange(of:c.historyDays) {_ in c.historyOffset=0;local.selection=nil;Task{await c.loadHistory()}}
        .onChange(of:c.historyOffset) {_ in local.selection=nil;Task{await c.loadHistory()}}
    }
    @ViewBuilder private func content(_ r:UsageHistoryReport)->some View {
        if r.remainingBytes>0 {
            VStack(alignment:.leading,spacing:6) {
                HStack {Text(L("正在导入已有日志"));Spacer();Text(L("剩余 ")+bytes(r.remainingBytes)).monospacedDigit()}
                ProgressView(value:r.scannedBytes,total:max(1,r.scannedBytes+r.remainingBytes))
                Text(L("可先查看已导入记录；更新过程不发送模型请求。")).foregroundStyle(.secondary)
            }.font(.system(size:12))
        }
        if r.summary.requests==0 {
            VStack(alignment:.leading,spacing:6) {
                Label(L("这个范围还没有已结束请求"),systemImage:"clock").font(.system(size:15,weight:.medium))
                Text(r.days==0 ? L("历史会在原生日志产生完成统计后出现。进行中的请求暂不计入。") : L("试试“全部”，或等待后续请求结束。缺少日期的旧日志只在“全部”中统计。"))
                    .font(.system(size:12)).foregroundStyle(.secondary)
            }.frame(maxWidth:.infinity,alignment:.leading).padding(16).background(Color.primary.opacity(0.04),in:RoundedRectangle(cornerRadius:12))
        }
        metrics(r.summary)
        trend(r)
        runtime(r)
        requests(r)
        VStack(alignment:.leading,spacing:6) {
            if r.summary.unknownTime>0 {
                Label(L("含 \(r.summary.unknownTime) 条时间不详的旧日志，仅“全部”范围包含；趋势图不把它们归入今天。"),systemImage:"info.circle")
            }
            Text(L(r.note))
            if r.summary.finishKnown>0 {Text(L("\(r.summary.finishKnown) 条提供结束原因，其中 \(r.summary.truncated) 条达到长度上限。长度上限不等于服务失败。"))}
            if r.skippedRecords>0 {Text(L("有 \(r.skippedRecords) 条截断或超大日志记录未纳入，统计覆盖不完整。")).foregroundStyle(.orange)}
            Text(L("采集开始于 ")+date(r.startedAt,full:true)+L(" · 本机统计约 ")+bytes(r.databaseBytes)+L("。只保存数字与模型来源；关闭应用后，下次会续读仍保留的引擎日志。"))
        }.font(.system(size:11)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
    }
    private func metrics(_ s:HistorySummary)->some View {
        let total=s.inputTokens==nil && s.outputTokens==nil ? nil : (s.inputTokens ?? 0)+(s.outputTokens ?? 0)
        return LazyVGrid(columns:columns,spacing:12) {
            metric(L("累计 token"),count(total),L("输入 ")+count(s.inputTokens)+L(" · 输出 ")+count(s.outputTokens))
            metric(L("已记录请求"),String(s.requests),L("仅包含有结束统计的请求"))
            metric(L("平均输出"),decimal(s.avgOutputTps),L("tok/s · 按处理时长加权"))
            metric(L("最高输出"),decimal(s.maxOutputTps),L("tok/s · 已记录单次均速"))
            metric(L("平均输入"),decimal(s.avgInputTps),L("tok/s · 按处理时长加权"))
            metric(L("P50 耗时"),seconds(s.p50Seconds),L("有耗时记录的中位数"))
            metric(L("P95 耗时"),seconds(s.p95Seconds),L("有耗时记录的 95 分位"))
            metric(L("缓存命中"),s.cacheRatio.map{String(format:"%.1f%%",100*$0)} ?? "—",L("基于 \(s.cacheRecords) 条缓存记录"))
        }
    }
    private func metric(_ title:String,_ value:String,_ caption:String)->some View {
        VStack(alignment:.leading,spacing:8) {
            Text(title).font(.system(size:12,weight:.medium)).foregroundStyle(.secondary)
            Text(value).font(.system(size:25,weight:.semibold)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
            Text(caption).font(.system(size:10)).foregroundStyle(.secondary).lineLimit(2).frame(minHeight:25,alignment:.topLeading)
        }.frame(maxWidth:.infinity,alignment:.leading).padding(13)
            .background(Color(nsColor:.controlBackgroundColor),in:RoundedRectangle(cornerRadius:12))
            .overlay(RoundedRectangle(cornerRadius:12).strokeBorder(Color.primary.opacity(0.06)))
    }
    private func trend(_ r:UsageHistoryReport)->some View {
        GroupBox {
            VStack(alignment:.leading,spacing:8) {
                HStack {Text(L("Token 用量趋势")).font(.system(size:14,weight:.semibold));Spacer();Text(L("仅包含日期可确定的记录")).font(.system(size:11)).foregroundStyle(.secondary)}
                if r.daily.contains(where:{$0.requests>0}) {
                    Chart(r.daily) {d in
                        BarMark(x:.value(L("日期"),d.day),y:.value("Token",d.inputTokens)).foregroundStyle(by:.value(L("方向"),L("输入")))
                        BarMark(x:.value(L("日期"),d.day),y:.value("Token",d.outputTokens)).foregroundStyle(by:.value(L("方向"),L("输出")))
                    }.chartForegroundStyleScale([L("输入"):Color.blue.opacity(0.7),L("输出"):Color.teal])
                        .chartXAxis(.hidden).frame(height:160)
                    HStack {Text(r.daily.first?.day ?? "");Spacer();Text(r.daily.last?.day ?? "")}.font(.system(size:10)).foregroundStyle(.secondary)
                } else {
                    Text(L("还没有可按日期绘图的请求；后续记录会自动形成趋势。")).font(.system(size:12)).foregroundStyle(.secondary).frame(maxWidth:.infinity,minHeight:110)
                }
            }.padding(8)
        }
    }
    private func runtime(_ r:UsageHistoryReport)->some View {
        GroupBox {
            VStack(alignment:.leading,spacing:12) {
                Text(L("延迟与运行观察")).font(.system(size:14,weight:.semibold))
                HStack(spacing:24) {
                    small(L("平均首 Token"),seconds(r.summary.avgTtftSeconds))
                    small(L("平均排队"),seconds(r.summary.avgQueueSeconds))
                    small(L("平均输出长度"),count(r.summary.avgOutputTokens)+" token")
                    small(L("活跃采样"),duration(r.summary.activeSeconds))
                }
                HStack {
                    Text(L("活跃时整机内存峰值 ")+(r.summary.peakSystemMemory.map{gib($0)} ?? "—"))
                    Spacer();Text(L("交换内存峰值 ")+(r.summary.peakSystemSwap.map{gib($0)} ?? "—"))
                }.font(.system(size:12)).monospacedDigit()
                if !r.agents.filter({$0.seconds>0}).isEmpty {
                    HStack {ForEach(r.agents.filter{$0.seconds>0}) {a in Text(a.agent+"  "+duration(a.seconds)).padding(.horizontal,8).padding(.vertical,5).background(.quaternary,in:Capsule())}}
                        .font(.system(size:11))
                }
                Text(L("活跃时长按刷新采样估算；Agent 时段可重叠，不按时段分摊 token。内存为整机值，包含其他应用。"))
                    .font(.system(size:10)).foregroundStyle(.secondary)
            }.frame(maxWidth:.infinity,alignment:.leading).padding(8)
        }
    }
    private func small(_ title:String,_ value:String)->some View {
        VStack(alignment:.leading,spacing:4) {Text(title).font(.system(size:11)).foregroundStyle(.secondary);Text(value).font(.system(size:14,weight:.medium)).monospacedDigit()}.frame(maxWidth:.infinity,alignment:.leading)
    }
    private func requests(_ r:UsageHistoryReport)->some View {
        VStack(alignment:.leading,spacing:10) {
            HStack {
                Text(L("请求明细")).font(.system(size:14,weight:.semibold));Spacer()
                Text(r.summary.requests==0 ? L("0 条") : L("\(r.offset+1)–\(r.offset+r.recent.count) / \(r.summary.requests) 条")).font(.system(size:11)).foregroundStyle(.secondary).monospacedDigit()
                Button(L("上一页")) {c.historyOffset=max(0,r.offset-80)}.disabled(r.offset==0 || c.historyLoading)
                Button(L("下一页")) {c.historyOffset=r.offset+80}.disabled(r.offset+r.recent.count>=r.summary.requests || c.historyLoading)
            }
            Text(L("已知日期优先，每页 80 条；点击列标题排序当前页。CSV 包含筛选后的全部记录。")).font(.system(size:10)).foregroundStyle(.secondary)
            Table(r.recent.sorted(using:local.sortOrder),selection:$local.selection,sortOrder:$local.sortOrder) {
                TableColumn(L("时间"),value:\.sortTime) {e in Text(e.completedAt.map{date($0)} ?? L("时间不详")).foregroundStyle(e.completedAt==nil ? .secondary : .primary)}.width(min:90,ideal:120)
                TableColumn(L("模型"),value:\.modelName) {e in Text(e.modelName).lineLimit(1).help(e.modelName)}.width(min:100,ideal:150)
                TableColumn(L("输入"),value:\.sortInput) {e in Text(integer(e.inputTokens)).monospacedDigit()}.width(min:50,ideal:68)
                TableColumn(L("输出"),value:\.sortOutput) {e in Text(integer(e.outputTokens)).monospacedDigit()}.width(min:50,ideal:65)
                TableColumn(L("输出 tok/s"),value:\.sortSpeed) {e in Text(decimal(e.outputTps)).monospacedDigit()}.width(min:65,ideal:85)
                TableColumn(L("耗时"),value:\.sortDuration) {e in Text(seconds(e.totalSeconds)).monospacedDigit()}.width(min:55,ideal:75)
                TableColumn(L("结束"),value:\.finishLabel) {e in Text(e.finishLabel).foregroundStyle(e.finishReason=="length" ? .orange : .secondary)}.width(min:65,ideal:80)
            }.frame(height:280)
            if let row=r.recent.first(where:{$0.id==local.selection}) {
                VStack(alignment:.leading,spacing:5) {
                    Text(L(row.source)+" · "+(row.timeBasis=="native" ? L("原生请求时间") : row.timeBasis=="observed" ? L("控制器采集时间") : L("原始日期不详"))).font(.system(size:11,weight:.medium))
                    Text(L("输入速度 ")+decimal(row.inputTps)+L(" tok/s  ·  缓存 ")+integer(row.cachedTokens)+L(" token  ·  首 Token ")+seconds(row.ttftSeconds)+L("  ·  排队 ")+seconds(row.queueSeconds)).font(.system(size:11)).monospacedDigit().foregroundStyle(.secondary)
                }.textSelection(.enabled)
            }
        }
    }
    private func decimal(_ v:Double?)->String {v.map{String(format:"%.1f",$0)} ?? "—"}
    private func integer(_ v:Double?)->String {v.map{Int($0).formatted()} ?? "—"}
    private func count(_ v:Double?)->String {
        guard let v=v else{return "—"}
        if v>=1_000_000{return String(format:"%.2fM",v/1_000_000)}
        if v>=10_000{return String(format:"%.1fK",v/1000)}
        return Int(v).formatted()
    }
    private func seconds(_ v:Double?)->String {v.map{String(format:"%.2f s",$0)} ?? "—"}
    private func bytes(_ v:Double)->String {ByteCountFormatter.string(fromByteCount:Int64(v),countStyle:.file)}
    private func duration(_ v:Double)->String {v>=3600 ? String(format:L("%.1f 小时"),v/3600) : String(format:L("%.1f 分钟"),v/60)}
    private func date(_ seconds:Double,full:Bool=false)->String {
        let f=DateFormatter();f.locale=Locale(identifier:UILanguage.resolved);f.timeZone=TimeZone.current;f.dateFormat=full ? "yyyy-MM-dd HH:mm" : "MM-dd HH:mm:ss";return f.string(from:Date(timeIntervalSince1970:seconds))
    }
}
