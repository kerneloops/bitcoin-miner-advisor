import SwiftUI
import WebKit

struct DashboardView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // Share the default cookie store so session cookie persists
        config.websiteDataStore = .default()

        // Hide the fkey bar and enable pinch-to-zoom via viewport meta
        let hideChrome = WKUserScript(
            source: """
            var s=document.createElement('style');
            s.textContent='#fkeyBar{display:none!important}main{padding-bottom:1rem!important}';
            document.head.appendChild(s);
            var vp=document.querySelector('meta[name=viewport]');
            if(vp){vp.setAttribute('content','width=device-width,initial-scale=1.0,user-scalable=yes,minimum-scale=0.5,maximum-scale=4.0');}
            """,
            injectionTime: .atDocumentEnd,
            forMainFrameOnly: true
        )
        config.userContentController.addUserScript(hideChrome)

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = true
        webView.scrollView.showsHorizontalScrollIndicator = false
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url == nil else { return }
        guard let url = URL(string: Config.baseURL) else { return }

        // Inject the session token as a cookie so the WKWebView is authenticated
        // without requiring the user to log in again through the web form.
        if let token = AuthManager.shared.sessionToken,
           let cookie = HTTPCookie(properties: [
               .name: "session",
               .value: token,
               .domain: url.host ?? "lapio.dev",
               .path: "/",
               .secure: true,
           ]) {
            webView.configuration.websiteDataStore.httpCookieStore.setCookie(cookie) {
                webView.load(URLRequest(url: url))
            }
        } else {
            webView.load(URLRequest(url: url))
        }
    }
}
