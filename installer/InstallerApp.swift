// Install LinkDrop (macOS). A small SwiftUI app that runs install-mac.sh step by step and shows progress.
// by @gabrielxreis_ - https://github.com/gabrielxreis/LinkDrop
import SwiftUI
import AppKit

struct Step: Identifiable {
    let id: String
    let title: String
    var state: State = .waiting
    enum State { case waiting, running, done, failed }
}

/// Which plugin this build installs: Info.plist key LDProduct ("resolve" or "premiere").
struct Product {
    let premiere = (Bundle.main.object(forInfoDictionaryKey: "LDProduct") as? String) == "premiere"
    var host: String { premiere ? "Premiere Pro" : "DaVinci Resolve" }
    var script: String { premiere ? "install-premiere-mac" : "install-mac" }
    var installTitle: String { premiere ? "Installing LinkDrop for Premiere from GitHub" : "Installing LinkDrop from GitHub" }
    var howTo: String {
        premiere
            ? "In Premiere Pro, open Window > Extensions > LinkDrop.\nIf Premiere was open, restart it once."
            : "In DaVinci Resolve, open Workspace > Scripts > LinkDrop.\nIf Resolve was open, restart it once.\n\nLinkDrop updates itself every time it opens."
    }
}
let product = Product()

@MainActor
final class Installer: ObservableObject {
    @Published var steps: [Step] = [
        Step(id: "detect", title: "Checking for an installed version"),
        Step(id: "clean", title: "Removing the old version"),
        Step(id: "python", title: "Checking Python 3"),
        Step(id: "ytdlp", title: "Downloading yt-dlp"),
        Step(id: "ffmpeg", title: "Downloading ffmpeg"),
        Step(id: "deno", title: "Downloading deno"),
        Step(id: "script", title: product.installTitle),
    ]
    @Published var phase: Phase = .ready
    @Published var error = ""
    @Published var installedVersion = ""
    enum Phase { case ready, running, done, failed }

    var progress: Double {
        Double(steps.filter { $0.state == .done }.count) / Double(steps.count)
    }

    /// One plain sentence for the current step (the installer doesn't list its internals).
    var status: String {
        guard let s = steps.first(where: { $0.state == .running }) ?? steps.first(where: { $0.state == .failed }) else {
            return "Preparing"
        }
        switch s.id {
        case "detect", "clean": return "Preparing"
        case "script": return "Adding LinkDrop to \(product.host)"
        default: return "Installing components"
        }
    }

    private var script: String { Bundle.main.path(forResource: product.script, ofType: "sh") ?? "" }

    func start() {
        guard phase != .running else { return }
        phase = .running
        error = ""
        for i in steps.indices { steps[i].state = .waiting }
        Task { await run() }
    }

    private func set(_ id: String, _ state: Step.State, title: String? = nil) {
        guard let i = steps.firstIndex(where: { $0.id == id }) else { return }
        steps[i].state = state
        if let title { steps[i] = Step(id: id, title: title, state: state) }
    }

    private func run() async {
        do {
            set("detect", .running)
            let found = try await sh("detect").trimmingCharacters(in: .whitespacesAndNewlines)
            set("detect", .done, title: found.isEmpty ? "No previous version found" : "Found LinkDrop \(found)")

            set("clean", .running, title: found.isEmpty ? "Preparing folders" : "Removing LinkDrop \(found)")
            _ = try await sh("clean")
            set("clean", .done)

            set("python", .running)
            if try await sh("python").contains("missing") {
                set("python", .running, title: "Installing Python 3 (LinkDrop needs it)")
                let pkg = try await sh("python-pkg").trimmingCharacters(in: .whitespacesAndNewlines)
                try await admin("installer -pkg '\(pkg)' -target /")
            }
            set("python", .done)

            for id in ["ytdlp", "ffmpeg", "deno", "script"] {
                set(id, .running)
                _ = try await sh(id)
                set(id, .done)
            }
            installedVersion = (try? await sh("detect"))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            phase = .done
            NSSound(named: "Glass")?.play()
        } catch {
            if let i = steps.firstIndex(where: { $0.state == .running }) { steps[i].state = .failed }
            self.error = error.localizedDescription
            phase = .failed
            NSSound(named: "Basso")?.play()
        }
    }

    private func sh(_ step: String) async throws -> String {
        let path = script
        return try await withCheckedThrowingContinuation { cont in
            DispatchQueue.global().async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/bin/bash")
                p.arguments = [path, step]
                let out = Pipe(), err = Pipe()
                p.standardOutput = out
                p.standardError = err
                do { try p.run() } catch { cont.resume(throwing: error); return }
                let o = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                let e = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                p.waitUntilExit()
                if p.terminationStatus == 0 { cont.resume(returning: o) }
                else {
                    let msg = e.trimmingCharacters(in: .whitespacesAndNewlines)
                    cont.resume(throwing: NSError(domain: "LinkDrop", code: Int(p.terminationStatus),
                        userInfo: [NSLocalizedDescriptionKey: msg.isEmpty ? "Step \(step) failed." : msg]))
                }
            }
        }
    }

    private func admin(_ command: String) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            DispatchQueue.global().async {
                var err: NSDictionary?
                let src = "do shell script \"\(command)\" with administrator privileges"
                NSAppleScript(source: src)?.executeAndReturnError(&err)
                if let err {
                    cont.resume(throwing: NSError(domain: "LinkDrop", code: 1, userInfo: [NSLocalizedDescriptionKey:
                        (err[NSAppleScript.errorMessage] as? String) ?? "Python wasn't installed."]))
                } else { cont.resume() }
            }
        }
    }
}

// ---------------------------------------------------------------- design ---
extension Color {
    static let bg0 = Color(red: 9/255, green: 10/255, blue: 15/255)
    static let electric = Color(red: 25/255, green: 81/255, blue: 252/255)
    static let light = Color(red: 105/255, green: 185/255, blue: 255/255)
    static let text2 = Color(red: 146/255, green: 157/255, blue: 184/255)
    static let ok = Color(red: 22/255, green: 217/255, blue: 139/255)
    static let rose = Color(red: 255/255, green: 71/255, blue: 126/255)
}

struct GlassButton: ButtonStyle {
    var primary = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: primary ? .semibold : .medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 18).padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(primary
                          ? AnyShapeStyle(LinearGradient(colors: [Color(red: 14/255, green: 42/255, blue: 156/255), .electric,
                                                                  Color(red: 55/255, green: 129/255, blue: 252/255)],
                                                         startPoint: .top, endPoint: .bottom))
                          : AnyShapeStyle(Color.white.opacity(0.06)))
            )
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(primary ? Color.white.opacity(0.55) : Color.light.opacity(0.25), lineWidth: 1))
            .shadow(color: primary ? Color.electric.opacity(0.55) : .clear, radius: 8)
            .opacity(configuration.isPressed ? 0.8 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

struct StepRow: View {
    let step: Step
    var body: some View {
        HStack(spacing: 10) {
            ZStack {
                switch step.state {
                case .waiting: Circle().strokeBorder(Color.white.opacity(0.18), lineWidth: 1.5)
                case .running: ProgressView().controlSize(.small).tint(.light)
                case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(Color.ok)
                case .failed: Image(systemName: "xmark.circle.fill").foregroundStyle(Color.rose)
                }
            }
            .frame(width: 16, height: 16)
            Text(step.title)
                .font(.system(size: 13, weight: step.state == .running ? .semibold : .regular))
                .foregroundStyle(step.state == .waiting ? Color.text2 : .white)
            Spacer()
        }
        .animation(.easeOut(duration: 0.2), value: step.state)
    }
}

struct ContentView: View {
    @StateObject var installer = Installer()

    var body: some View {
        ZStack {
            Color.bg0
            RadialGradient(colors: [Color.electric.opacity(0.45), Color(red: 3/255, green: 25/255, blue: 91/255).opacity(0.3), .clear],
                           center: UnitPoint(x: 0.95, y: -0.1), startRadius: 0, endRadius: 520)
            VStack(alignment: .leading, spacing: 18) {
                HStack(spacing: 14) {
                    Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 56, height: 56)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title).font(.system(size: 22, weight: .bold)).foregroundStyle(.white)
                        Text(subtitle).font(.system(size: 13)).foregroundStyle(Color.text2)
                    }
                }
                switch installer.phase {
                case .ready: readyView
                case .running, .failed: progressView
                case .done: doneView
                }
                Spacer(minLength: 0)
                HStack {
                    Button("@gabrielxreis_") { NSWorkspace.shared.open(URL(string: "https://instagram.com/gabrielxreis_")!) }
                        .buttonStyle(.plain).font(.system(size: 12, weight: .semibold)).foregroundStyle(Color.light)
                    Spacer()
                    buttons
                }
            }
            .padding(26)
        }
        .frame(width: 480, height: 330)
        .preferredColorScheme(.dark)
        .onAppear { if CommandLine.arguments.contains("--auto") { installer.start() } }
    }

    var title: String {
        switch installer.phase {
        case .done: return product.premiere ? "LinkDrop for Premiere is installed" : "LinkDrop is installed"
        case .failed: return "Installation stopped"
        case .running: return product.premiere ? "Installing LinkDrop for Premiere" : "Installing LinkDrop"
        case .ready: return product.premiere ? "Install LinkDrop for Premiere" : "Install LinkDrop"
        }
    }

    var subtitle: String {
        switch installer.phase {
        case .done: return installer.installedVersion.isEmpty ? "Ready in \(product.host)." : "Version \(installer.installedVersion), ready in \(product.host)."
        case .failed: return "Something went wrong. You can try again."
        case .running: return "This takes about a minute."
        case .ready: return "Download links straight into \(product.host)."
        }
    }

    var readyView: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("LinkDrop will be added to \(product.host) with everything it needs. Any previous version is replaced. Your settings are kept.")
                .font(.system(size: 13)).foregroundStyle(Color.text2).fixedSize(horizontal: false, vertical: true)
        }
    }

    var progressView: some View {
        VStack(alignment: .leading, spacing: 14) {
            ProgressView(value: max(0.04, installer.progress)).tint(.light)
                .animation(.easeOut(duration: 0.35), value: installer.progress)
            HStack(spacing: 10) {
                if installer.phase == .running {
                    ProgressView().controlSize(.small).tint(.light)
                } else {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(Color.rose)
                }
                Text(installer.phase == .running ? installer.status + "..." : "Installation didn't finish")
                    .font(.system(size: 13, weight: .medium)).foregroundStyle(.white)
                    .contentTransition(.opacity)
                    .animation(.easeOut(duration: 0.2), value: installer.status)
            }
            if !installer.error.isEmpty {
                Text(installer.error).font(.system(size: 12)).foregroundStyle(Color.rose)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    var doneView: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill").font(.system(size: 26)).foregroundStyle(Color.ok)
                    .shadow(color: Color.ok.opacity(0.6), radius: 8)
                Text("All set").font(.system(size: 17, weight: .semibold)).foregroundStyle(.white)
            }
            Text(product.howTo)
                .font(.system(size: 13)).foregroundStyle(Color.text2).fixedSize(horizontal: false, vertical: true)
        }
        .transition(.opacity)
    }

    @ViewBuilder var buttons: some View {
        switch installer.phase {
        case .ready:
            Button("Install") { withAnimation(.easeOut(duration: 0.2)) { installer.start() } }
                .buttonStyle(GlassButton(primary: true)).keyboardShortcut(.defaultAction)
        case .running:
            EmptyView()
        case .failed:
            HStack {
                Button("Quit") { NSApp.terminate(nil) }.buttonStyle(GlassButton())
                Button("Try Again") { installer.start() }.buttonStyle(GlassButton(primary: true))
            }
        case .done:
            Button("Done") { NSApp.terminate(nil) }.buttonStyle(GlassButton(primary: true)).keyboardShortcut(.defaultAction)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct InstallLinkDropApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        WindowGroup(product.premiere ? "Install LinkDrop for Premiere" : "Install LinkDrop") { ContentView() }
            .windowResizability(.contentSize)
            .windowStyle(.hiddenTitleBar)
    }
}
