import SwiftUI

// MARK: - Colour palette (matches lapio.dev CSS vars)
private let termBg      = Color(hex: "#0a0a0a")
private let termSurface = Color(hex: "#111111")
private let termSurface2 = Color(hex: "#1a1a1a")
private let termGreen   = Color(hex: "#00d26a")
private let termGreenDim = Color(hex: "#00a854")
private let termText    = Color(hex: "#e8e8e8")
private let termMuted   = Color(hex: "#666666")
private let termBorder  = Color(hex: "#2a2a2a")

struct ChatView: View {
    @ObservedObject var viewModel: ChatViewModel
    @State private var inputText = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        NavigationStack {
            ZStack {
                termBg.ignoresSafeArea()
                VStack(spacing: 0) {
                    messageList
                    Divider().background(termBorder)
                    inputBar
                }
            }
            .navigationTitle("LAPIO ADVISOR")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbarBackground(termSurface, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
        .onAppear { viewModel.startPolling() }
        .onDisappear { viewModel.stopPolling() }
    }

    // MARK: - Message List

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 10) {
                    if viewModel.messages.isEmpty {
                        Text("Ask about your positions, signals, or market conditions.")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundStyle(termMuted)
                            .multilineTextAlignment(.center)
                            .padding(32)
                    }
                    ForEach(viewModel.messages) { msg in
                        MessageBubble(message: msg)
                            .id(msg.id)
                    }
                    if viewModel.isLoading {
                        HStack(spacing: 8) {
                            ProgressView()
                                .tint(termGreen)
                                .scaleEffect(0.75)
                            Text("thinking…")
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(termMuted)
                            Spacer()
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
            }
            .onChange(of: viewModel.messages.count) { _, _ in
                if let last = viewModel.messages.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    // MARK: - Input Bar

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 8) {
            ZStack(alignment: .topLeading) {
                if inputText.isEmpty {
                    Text("▋ Ask anything…")
                        .font(.system(size: 13, design: .monospaced))
                        .foregroundStyle(termMuted)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 9)
                        .allowsHitTesting(false)
                }
                TextField("", text: $inputText, axis: .vertical)
                    .lineLimit(1...5)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(termGreen)
                    .tint(termGreen)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 9)
                    .focused($inputFocused)
            }
            .background(termSurface2)
            .overlay(Rectangle().stroke(inputFocused ? termGreenDim : termBorder, lineWidth: 1))

            Button {
                let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { return }
                inputText = ""
                Task { await viewModel.send(text) }
            } label: {
                Text("SEND")
                    .font(.system(size: 10, design: .monospaced).weight(.bold))
                    .tracking(1)
                    .foregroundStyle(termBg)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .background(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isLoading ? termGreenDim : termGreen)
            }
            .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isLoading)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(termBg)
    }
}

// MARK: - Bubble

private struct MessageBubble: View {
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            if isUser { Spacer(minLength: 48) }

            VStack(alignment: isUser ? .trailing : .leading, spacing: 4) {
                // Role label
                Text(isUser ? "YOU" : "LAPIO")
                    .font(.system(size: 9, design: .monospaced).weight(.bold))
                    .foregroundStyle(isUser ? termMuted : termGreen)
                    .tracking(1)
                    .padding(.horizontal, 2)

                Text(message.text.strippingHTML)
                    .font(.system(size: 13, design: .monospaced))
                    .foregroundStyle(isUser ? termMuted : termText)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 9)
                    .background(isUser ? termSurface2 : termSurface)
                    .overlay(
                        Rectangle().stroke(isUser ? termBorder : termGreenDim.opacity(0.4), lineWidth: 1)
                    )

                Text(shortTime(message.ts))
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(termMuted)
                    .padding(.horizontal, 2)
            }

            if !isUser { Spacer(minLength: 48) }
        }
    }

    private func shortTime(_ iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: iso) else { return iso }
        let display = DateFormatter()
        display.dateFormat = "HH:mm"
        return display.string(from: date)
    }
}

// MARK: - Helpers

private extension String {
    var strippingHTML: String {
        replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
    }
}

private extension Color {
    init(hex: String) {
        let h = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int = UInt64()
        Scanner(string: h).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255
        let g = Double((int >> 8)  & 0xFF) / 255
        let b = Double(int         & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
