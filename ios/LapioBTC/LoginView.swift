import SwiftUI

struct LoginView: View {
    @ObservedObject var auth: AuthManager
    @State private var username = ""
    @State private var password = ""
    @State private var isRegistering = false
    @State private var isLoading = false
    @State private var errorMessage: String? = nil

    private let green = Color(red: 0.2, green: 0.88, blue: 0.42)
    private let dimGreen = Color(red: 0.14, green: 0.62, blue: 0.3)
    private let surface = Color(white: 0.08)
    private let fieldBg = Color(white: 0.05)
    private let fieldBorder = Color(white: 0.18)

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 0) {
                Spacer()
                VStack(alignment: .leading, spacing: 0) {
                    // Header
                    Text("HASH & BURN")
                        .font(.system(.caption, design: .monospaced).weight(.bold))
                        .foregroundColor(green)
                        .tracking(3)
                        .padding(.bottom, 4)
                    Text("LAPIO TRADING TERMINAL")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.gray)
                        .tracking(2)
                        .padding(.bottom, isRegistering ? 6 : 24)

                    if isRegistering {
                        Text("BETA REGISTRATION")
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundColor(Color(red: 1, green: 0.36, blue: 0.36))
                            .tracking(2)
                            .padding(.bottom, 20)
                    }

                    // Username
                    fieldLabel("USERNAME")
                    terminalField($username, isSecure: false, tag: 0)
                        .padding(.bottom, 16)

                    // Password
                    fieldLabel("PASSWORD")
                    terminalField($password, isSecure: true, tag: 1)
                        .padding(.bottom, 20)

                    // Error
                    if let err = errorMessage {
                        Text(err)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundColor(Color(red: 1, green: 0.23, blue: 0.23))
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.bottom, 12)
                    }

                    // Submit button
                    Button(action: submit) {
                        HStack(spacing: 8) {
                            if isLoading {
                                ProgressView()
                                    .progressViewStyle(.circular)
                                    .scaleEffect(0.65)
                                    .tint(Color.black)
                            }
                            Text(isRegistering ? "REQUEST ACCESS" : "ENTER AT YOUR OWN RISK")
                                .font(.system(size: 11, design: .monospaced).weight(.bold))
                                .tracking(2)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(isLoading ? dimGreen : green)
                        .foregroundColor(.black)
                    }
                    .disabled(isLoading)
                    .padding(.bottom, 14)

                    // Toggle login/register
                    Button(action: {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            isRegistering.toggle()
                        }
                        errorMessage = nil
                    }) {
                        Text(isRegistering ? "← Back to login" : "Request beta access →")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundColor(.gray)
                            .tracking(1)
                    }
                }
                .padding(28)
                .background(surface)
                .overlay(Rectangle().stroke(Color(white: 0.14), lineWidth: 1))
                .frame(maxWidth: 340)
                Spacer()
            }
            .padding(.horizontal, 24)
        }
    }

    // MARK: - Sub-views

    private func fieldLabel(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 9, design: .monospaced))
            .foregroundColor(.gray)
            .tracking(2)
            .padding(.bottom, 6)
    }

    @ViewBuilder
    private func terminalField(_ binding: Binding<String>, isSecure: Bool, tag: Int) -> some View {
        ZStack(alignment: .leading) {
            Text("▋")
                .font(.system(size: 14, design: .monospaced))
                .foregroundColor(green)
                .padding(.leading, 10)
                .allowsHitTesting(false)

            if isSecure {
                SecureField("", text: binding)
                    .font(.system(.body, design: .monospaced))
                    .foregroundColor(green)
                    .padding(.leading, 26)
                    .padding(.trailing, 10)
                    .padding(.vertical, 10)
            } else {
                TextField("", text: binding)
                    .font(.system(.body, design: .monospaced))
                    .foregroundColor(green)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .padding(.leading, 26)
                    .padding(.trailing, 10)
                    .padding(.vertical, 10)
            }
        }
        .background(fieldBg)
        .overlay(Rectangle().stroke(fieldBorder, lineWidth: 1))
    }

    // MARK: - Actions

    private func submit() {
        errorMessage = nil
        isLoading = true
        Task {
            defer { isLoading = false }
            do {
                if isRegistering {
                    try await auth.register(username: username, password: password)
                } else {
                    try await auth.login(username: username, password: password)
                }
            } catch AuthError.invalidCredentials {
                errorMessage = "Invalid username or password."
            } catch AuthError.betaFull {
                errorMessage = "Beta is full — check back later."
            } catch AuthError.usernameTaken {
                errorMessage = "Username already taken."
            } catch AuthError.passwordTooShort {
                errorMessage = "Password must be at least 8 characters."
            } catch {
                errorMessage = "Network error. Try again."
            }
        }
    }
}
