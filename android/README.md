# Samruddhi Finance Android wrapper

This is a lightweight Android WebView wrapper for the hosted app at:

https://samruddhi-fin.onrender.com

## Build an APK

1. Push the Android wrapper files to GitHub.
2. Open **Actions → Build Android APK**.
3. Run the workflow manually, or let it run after changes to the wrapper.
4. Download the `samruddhi-finance-debug-apk` artifact from the successful workflow run.
5. Install `app-debug.apk` on an Android device. This is a debug-signed APK for testing/sideloading.

## Web compatibility

- JavaScript and DOM storage enabled for app interactions and saved theme preferences.
- Responsive viewport settings so the existing site CSS drives mobile layout.
- Same-host navigation, login redirects and in-app routes stay in the WebView.
- `target=_blank` / JavaScript popups open in an in-app WebView dialog; same-host redirects return to the main view.
- External websites and app links are handed to Android.
- File chooser support is enabled for HTML upload controls.
- HTTPS-only network loading; no cleartext or certificate-validation bypass.

The APK is a wrapper, not an offline copy. An internet connection is required and the hosted service must be available.
