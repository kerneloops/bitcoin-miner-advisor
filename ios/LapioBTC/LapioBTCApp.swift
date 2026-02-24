import SwiftUI

@main
struct LapioBTCApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    init() {
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(red: 0.067, green: 0.067, blue: 0.067, alpha: 1) // #111111
        appearance.stackedLayoutAppearance.normal.iconColor = UIColor(white: 0.4, alpha: 1)
        appearance.stackedLayoutAppearance.normal.titleTextAttributes = [
            .foregroundColor: UIColor(white: 0.4, alpha: 1),
            .font: UIFont.monospacedSystemFont(ofSize: 9, weight: .regular),
        ]
        appearance.stackedLayoutAppearance.selected.iconColor = UIColor(red: 0, green: 0.824, blue: 0.416, alpha: 1) // #00d26a
        appearance.stackedLayoutAppearance.selected.titleTextAttributes = [
            .foregroundColor: UIColor(red: 0, green: 0.824, blue: 0.416, alpha: 1),
            .font: UIFont.monospacedSystemFont(ofSize: 9, weight: .bold),
        ]
        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
