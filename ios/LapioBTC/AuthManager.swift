import Foundation
import Security

enum AuthError: Error {
    case invalidCredentials
    case betaFull
    case usernameTaken
    case passwordTooShort
    case networkError
}

@MainActor
class AuthManager: ObservableObject {
    static let shared = AuthManager()

    @Published var isAuthenticated = false
    @Published var username: String = ""
    @Published var userId: String = ""

    private let baseURL = Config.baseURL
    private let keychainService = "dev.lapio.miner"
    private let keychainAccount = "session_token"

    private init() {}

    var sessionToken: String? { loadToken() }

    // MARK: - Cold launch validation

    func checkStoredSession() async {
        guard let token = loadToken() else {
            isAuthenticated = false
            return
        }
        guard let url = URL(string: "\(baseURL)/api/auth/me") else { return }
        var req = URLRequest(url: url)
        req.setValue(token, forHTTPHeaderField: "X-Session-Token")
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse, http.statusCode == 200 else {
                isAuthenticated = false
                return
            }
            struct MeResponse: Decodable { let username: String; let user_id: String }
            let me = try JSONDecoder().decode(MeResponse.self, from: data)
            self.username = me.username
            self.userId = me.user_id
            self.isAuthenticated = true
        } catch {
            isAuthenticated = false
        }
    }

    // MARK: - Login

    func login(username: String, password: String) async throws {
        guard let url = URL(string: "\(baseURL)/api/auth/login") else {
            throw AuthError.networkError
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONEncoder().encode(["username": username, "password": password])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else { throw AuthError.networkError }
            if http.statusCode == 401 { throw AuthError.invalidCredentials }
            guard http.statusCode == 200 else { throw AuthError.networkError }
            struct LoginResponse: Decodable { let token: String; let username: String; let user_id: String }
            let result = try JSONDecoder().decode(LoginResponse.self, from: data)
            saveToken(result.token)
            self.username = result.username
            self.userId = result.user_id
            self.isAuthenticated = true
        } catch let e as AuthError { throw e }
        catch { throw AuthError.networkError }
    }

    // MARK: - Register

    func register(username: String, password: String) async throws {
        guard let url = URL(string: "\(baseURL)/api/auth/register") else {
            throw AuthError.networkError
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONEncoder().encode(["username": username, "password": password])
        do {
            let (data, resp) = try await URLSession.shared.data(for: req)
            guard let http = resp as? HTTPURLResponse else { throw AuthError.networkError }
            if http.statusCode == 403 { throw AuthError.betaFull }
            if http.statusCode == 409 { throw AuthError.usernameTaken }
            if http.statusCode == 422 { throw AuthError.passwordTooShort }
            guard http.statusCode == 200 else { throw AuthError.networkError }
            struct RegisterResponse: Decodable { let token: String; let username: String; let user_id: String }
            let result = try JSONDecoder().decode(RegisterResponse.self, from: data)
            saveToken(result.token)
            self.username = result.username
            self.userId = result.user_id
            self.isAuthenticated = true
        } catch let e as AuthError { throw e }
        catch { throw AuthError.networkError }
    }

    // MARK: - Logout

    func logout() async {
        guard let token = loadToken(),
              let url = URL(string: "\(baseURL)/api/auth/logout") else {
            clearSession()
            return
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(token, forHTTPHeaderField: "X-Session-Token")
        _ = try? await URLSession.shared.data(for: req)
        clearSession()
    }

    private func clearSession() {
        deleteToken()
        username = ""
        userId = ""
        isAuthenticated = false
    }

    // MARK: - Keychain

    private func saveToken(_ token: String) {
        guard let data = token.data(using: .utf8) else { return }
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: keychainService,
            kSecAttrAccount: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
        var attrs = query
        attrs[kSecValueData] = data
        SecItemAdd(attrs as CFDictionary, nil)
    }

    private func loadToken() -> String? {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: keychainService,
            kSecAttrAccount: keychainAccount,
            kSecReturnData: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        SecItemCopyMatching(query as CFDictionary, &result)
        guard let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private func deleteToken() {
        let query: [CFString: Any] = [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: keychainService,
            kSecAttrAccount: keychainAccount,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
