import SwiftUI

struct ContentView: View {
    @StateObject private var auth = AuthManager.shared
    @StateObject private var chatVM = ChatViewModel()

    var body: some View {
        Group {
            if auth.isAuthenticated {
                TabView {
                    DashboardView()
                        .tabItem {
                            Label("Dashboard", systemImage: "chart.line.uptrend.xyaxis")
                        }

                    ChatView(viewModel: chatVM)
                        .tabItem {
                            Label("LAPIO ADVISOR", systemImage: "bubble.left.and.bubble.right")
                        }
                }
                .toolbarBackground(Color(red: 0.067, green: 0.067, blue: 0.067), for: .tabBar)
                .toolbarBackground(.visible, for: .tabBar)
                .toolbarColorScheme(.dark, for: .tabBar)
            } else {
                LoginView(auth: auth)
            }
        }
        .task {
            await auth.checkStoredSession()
        }
    }
}
