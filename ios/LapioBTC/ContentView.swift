import SwiftUI

struct ContentView: View {
    @StateObject private var chatVM = ChatViewModel()

    var body: some View {
        TabView {
            DashboardView()
                .tabItem {
                    Label("Dashboard", systemImage: "chart.line.uptrend.xyaxis")
                }

            ChatView(viewModel: chatVM)
                .tabItem {
                    Label("Chat", systemImage: "bubble.left.and.bubble.right")
                }
        }
    }
}
