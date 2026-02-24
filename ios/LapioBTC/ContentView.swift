import SwiftUI

struct ContentView: View {
    @StateObject private var auth = AuthManager.shared
    @StateObject private var chatVM = ChatViewModel()

    var body: some View {
        Group {
            if auth.isAuthenticated {
                TabView {
                    NavigationStack {
                        DashboardView()
                            .background(Color(red: 0.04, green: 0.04, blue: 0.04))
                            .navigationTitle("LAPIO")
                            .navigationBarTitleDisplayMode(.inline)
                            .toolbarColorScheme(.dark, for: .navigationBar)
                            .toolbarBackground(Color(red: 0.067, green: 0.067, blue: 0.067), for: .navigationBar)
                            .toolbarBackground(.visible, for: .navigationBar)
                            .toolbar {
                                ToolbarItem(placement: .navigationBarTrailing) {
                                    logoutButton
                                }
                            }
                    }
                    .tabItem {
                        Label("Dashboard", systemImage: "chart.line.uptrend.xyaxis")
                    }
                    .environment(\.symbolVariants, .none)

                    ChatView(viewModel: chatVM, onLogout: { Task { await auth.logout() } })
                        .tabItem {
                            Label("LAPIO ADVISOR", systemImage: "bubble.left.and.bubble.right")
                        }
                        .environment(\.symbolVariants, .none)
                }
                .toolbarBackground(Color(red: 0.067, green: 0.067, blue: 0.067), for: .tabBar)
                .toolbarBackground(.visible, for: .tabBar)
                .toolbarColorScheme(.dark, for: .tabBar)
            } else {
                LoginView(auth: auth)
            }
        }
        .preferredColorScheme(.dark)
        .task {
            await auth.checkStoredSession()
        }
    }

    private var logoutButton: some View {
        Button {
            Task { await auth.logout() }
        } label: {
            Text("LOGOUT")
                .font(.system(size: 10, design: .monospaced).weight(.bold))
                .foregroundStyle(Color(red: 1, green: 0.36, blue: 0.36))
                .tracking(1)
        }
    }
}
