import Foundation

struct ChatMessage: Identifiable, Decodable {
    let id: Int
    let role: String
    let text: String
    let ts: String
}

@MainActor
class ChatViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var isLoading = false
    @Published var errorMessage: String? = nil

    private var pollTask: Task<Void, Never>?
    private let baseURL = Config.baseURL
    private let password = Config.appPassword

    // MARK: - Polling

    func startPolling() {
        stopPolling()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.fetchMessages()
                try? await Task.sleep(nanoseconds: 5_000_000_000) // 5s
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: - Fetch

    func fetchMessages() async {
        guard let url = URL(string: "\(baseURL)/api/chat/messages?limit=100") else { return }
        var req = URLRequest(url: url)
        req.setValue(password, forHTTPHeaderField: "X-App-Password")
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            let decoded = try JSONDecoder().decode([ChatMessage].self, from: data)
            messages = decoded
            errorMessage = nil
        } catch {
            errorMessage = "Could not load messages."
        }
    }

    // MARK: - Send

    func send(_ text: String) async {
        guard let url = URL(string: "\(baseURL)/api/chat/send") else { return }
        isLoading = true
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.setValue(password, forHTTPHeaderField: "X-App-Password")
        req.httpBody = try? JSONEncoder().encode(["text": text])
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            struct SendResponse: Decodable { let ok: Bool; let reply: String }
            let resp = try JSONDecoder().decode(SendResponse.self, from: data)
            // Refresh full list (includes the user message the server stored)
            await fetchMessages()
            errorMessage = nil
            _ = resp.reply // already in fetched messages
        } catch {
            errorMessage = "Send failed: \(error.localizedDescription)"
        }
        isLoading = false
    }
}
