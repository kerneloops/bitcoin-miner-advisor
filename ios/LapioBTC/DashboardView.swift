import SwiftUI
import WebKit

struct DashboardView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // Share the default cookie store so session cookie persists
        config.websiteDataStore = .default()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = true
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url == nil else { return }
        if let url = URL(string: Config.baseURL) {
            webView.load(URLRequest(url: url))
        }
    }
}
