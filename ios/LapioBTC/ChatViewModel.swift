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
        if let token = AuthManager.shared.sessionToken {
            req.setValue(token, forHTTPHeaderField: "X-Session-Token")
        }
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode == 401 {
                await AuthManager.shared.logout()
                return
            }
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
        if let token = AuthManager.shared.sessionToken {
            req.setValue(token, forHTTPHeaderField: "X-Session-Token")
        }
        req.httpBody = try? JSONEncoder().encode(["text": text])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            if let http = resp as? HTTPURLResponse, http.statusCode == 401 {
                await AuthManager.shared.logout()
                return
            }
            struct SendResponse: Decodable { let ok: Bool; let reply: String }
            let _ = try JSONDecoder().decode(SendResponse.self, from: data)
            await fetchMessages()
            errorMessage = nil
        } catch {
            errorMessage = "Send failed: \(error.localizedDescription)"
        }
        isLoading = false
    }
}
