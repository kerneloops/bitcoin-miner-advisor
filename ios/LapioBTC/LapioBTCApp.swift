import SwiftUI

@main
struct LapioBTCApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    init() {
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(red: 0.067, green: 0.067, blue: 0.067, alpha: 1) // #111111
        let green = UIColor(red: 0, green: 0.824, blue: 0.416, alpha: 1) // #00d26a
        let muted = UIColor(white: 0.55, alpha: 1)

        appearance.stackedLayoutAppearance.normal.iconColor = muted
        appearance.stackedLayoutAppearance.normal.titleTextAttributes = [
            .foregroundColor: muted,
            .font: UIFont.monospacedSystemFont(ofSize: 13, weight: .regular),
        ]
        appearance.stackedLayoutAppearance.selected.iconColor = green
        appearance.stackedLayoutAppearance.selected.titleTextAttributes = [
            .foregroundColor: green,
            .font: UIFont.monospacedSystemFont(ofSize: 13, weight: .bold),
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
