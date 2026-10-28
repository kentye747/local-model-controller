import SwiftUI
import AppKit

private struct ControllerMotionKey:EnvironmentKey {static let defaultValue=true}
extension EnvironmentValues {
    var controllerMotion:Bool {
        get {self[ControllerMotionKey.self]}
        set {self[ControllerMotionKey.self]=newValue}
    }
}
enum ControlEmphasis {case primary,secondary,quiet}
@MainActor final class HoverFeedback:ObservableObject {@Published var inside=false}
struct FeedbackButtonStyle:ButtonStyle {
    var emphasis:ControlEmphasis = .secondary
    var working=false
    func makeBody(configuration:Configuration)->some View {FeedbackButtonSurface(label:configuration.label,pressed:configuration.isPressed,emphasis:emphasis,working:working)}
}
private struct FeedbackButtonSurface<Label:View>:View {
    let label:Label
    let pressed:Bool
    let emphasis:ControlEmphasis
    let working:Bool
    @StateObject private var hover=HoverFeedback()
    @Environment(\.isEnabled) private var enabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.controllerMotion) private var motion
    @Environment(\.colorScheme) private var scheme
    @Environment(\.isFocused) private var focused
    private var animation:Animation? {motion && !reduceMotion ? .easeOut(duration:0.12) : nil}
    private var primaryText:Color {
        guard let c=NSColor.controlAccentColor.usingColorSpace(.sRGB) else{return .white}
        func linear(_ v:CGFloat)->Double {let n=Double(v);return n<=0.04045 ? n/12.92 : pow((n+0.055)/1.055,2.4)}
        let luminance=0.2126*linear(c.redComponent)+0.7152*linear(c.greenComponent)+0.0722*linear(c.blueComponent)
        return luminance>0.179 ? .black : .white
    }
    private var fill:Color {
        if emphasis == .primary {return Color.accentColor.opacity(pressed ? 0.82 : 1)}
        return Color.primary.opacity(pressed ? 0.10 : hover.inside && enabled ? 0.065 : emphasis == .quiet ? 0 : 0.035)
    }
    var body:some View {
        label.font(.system(size:12,weight:emphasis == .primary ? .semibold : .medium))
            .padding(.horizontal,emphasis == .quiet ? 8 : 12).padding(.vertical,7).frame(minHeight:30)
            .foregroundStyle(emphasis == .primary ? primaryText : Color.primary)
            .background(fill,in:RoundedRectangle(cornerRadius:8))
            .overlay(RoundedRectangle(cornerRadius:8).strokeBorder(Color.primary.opacity(emphasis == .quiet || emphasis == .primary ? 0 : 0.11)))
            .overlay(RoundedRectangle(cornerRadius:10).strokeBorder(Color.accentColor.opacity(focused ? 0.85 : 0),lineWidth:2).padding(-3))
            .opacity(enabled || working ? 1 : 0.38)
            .scaleEffect(pressed && motion && !reduceMotion && enabled ? 0.975 : 1)
            .contentShape(RoundedRectangle(cornerRadius:8))
            .onHover {hover.inside=$0}
            .animation(animation,value:pressed).animation(animation,value:hover.inside)
    }
}
struct OperationButtonLabel:View {
    let title:String
    let symbol:String
    let activeTitle:String
    let active:Bool
    var body:some View {
        HStack(spacing:6) {
            if active {ProgressView().controlSize(.mini)}
            else {Image(systemName:symbol).font(.system(size:11,weight:.medium)).accessibilityHidden(true)}
            Text(active ? activeTitle : title).fixedSize()
        }.frame(minWidth:43)
    }
}
struct ControlNavigation:View {
    @ObservedObject var c:Controller
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.controllerMotion) private var motion
    private let names=[L("模型管理"),L("Agent 分配"),L("内存设置"),L("使用历史")]
    private let symbols=["square.stack.3d.up","person.2","memorychip","chart.xyaxis.line"]
    private func navigationLabel(_ i:Int)->some View {
        Label(names[i],systemImage:symbols[i])
            .font(.system(size:12,weight:c.tab==i ? Font.Weight.semibold : Font.Weight.regular))
            .foregroundColor(c.tab==i ? Color.primary : Color.secondary)
            .frame(maxWidth:.infinity).padding(.vertical,10).contentShape(Rectangle())
    }
    private func navigationButton(_ i:Int)->some View {
        Button {c.tab=i} label:{navigationLabel(i)}.buttonStyle(.plain)
            .keyboardShortcut(KeyEquivalent(Character(String(i+1))),modifiers:.command)
            .accessibilityAddTraits(c.tab==i ? AccessibilityTraits.isSelected : [])
            .help(names[i]+" · ⌘"+String(i+1))
    }
    var body:some View {
        HStack(spacing:0) {
            ForEach(0..<4,id:\.self) {i in navigationButton(i)}
        }.background {
            GeometryReader {g in
                RoundedRectangle(cornerRadius:9).fill(Color(nsColor:.controlBackgroundColor))
                    .overlay(RoundedRectangle(cornerRadius:9).strokeBorder(Color.primary.opacity(0.07)))
                    .frame(width:g.size.width/4).offset(x:g.size.width*CGFloat(c.tab)/4)
                    .animation(motion && !reduceMotion ? .easeOut(duration:0.22) : nil,value:c.tab)
            }.allowsHitTesting(false).accessibilityHidden(true)
        }.padding(4).background(Color.primary.opacity(0.045),in:RoundedRectangle(cornerRadius:12))
            .accessibilityElement(children:.contain).accessibilityLabel(L("控制区"))
    }
}
@MainActor final class InteractionPreviewState:ObservableObject {
    @Published var busy=false
    @Published var expanded=false
    @Published var complete=false
    private var run:Task<Void,Never>?
    func start() {
        guard !busy else{return}
        busy=true;complete=false
        run=Task {
            do {try await Task.sleep(nanoseconds:1_800_000_000)} catch{return}
            guard !Task.isCancelled else{return}
            busy=false;complete=true
        }
    }
    func stop() {run?.cancel();run=nil;busy=false}
}
struct InteractionPreview:View {
    @ObservedObject var c:Controller
    @StateObject private var state=InteractionPreviewState()
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.controllerMotion) private var motion
    private var animation:Animation? {motion && !reduceMotion ? .easeOut(duration:0.20) : nil}
    var body:some View {
        VStack(alignment:.leading,spacing:18) {
            HStack {Text(L("交互预览")).font(.system(size:17,weight:.semibold));Spacer();Button {c.showInteractionPreview=false} label:{Image(systemName:"xmark")}.buttonStyle(FeedbackButtonStyle(emphasis:.quiet)).accessibilityLabel(L("关闭交互预览"))}
            Text(L("体验按钮、展开和完成反馈。演示不调用模型。")).font(.system(size:12)).foregroundStyle(.secondary)
            Button {state.start()} label:{OperationButtonLabel(title:L("体验按钮反馈"),symbol:"hand.tap",activeTitle:L("正在演示…"),active:state.busy)}.buttonStyle(FeedbackButtonStyle(emphasis:.primary,working:state.busy)).disabled(state.busy)
            Button {withAnimation(animation){state.expanded.toggle()}} label:{HStack{Text(state.expanded ? L("收起详情") : L("展开详情"));Spacer();Image(systemName:"chevron.down").rotationEffect(.degrees(state.expanded ? 180 : 0))}}
                .buttonStyle(FeedbackButtonStyle()).accessibilityValue(state.expanded ? L("已展开") : L("已收起"))
            if state.expanded {Text(L("补充信息在当前位置展开，主要操作始终可见。再次点击即可收起。")).font(.system(size:12)).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true).transition(.opacity)}
            if state.complete {Label(L("演示完成"),systemImage:"checkmark.circle.fill").foregroundStyle(.green).font(.system(size:12)).transition(.opacity)}
            if reduceMotion {Text(L("系统已开启“减少动态效果”，保留文字与颜色反馈。")).font(.system(size:11)).foregroundStyle(.secondary)}
        }.padding(22).frame(width:350).animation(animation,value:state.complete).environment(\.controllerMotion,c.motionEnabled).onDisappear{state.stop()}
    }
}
