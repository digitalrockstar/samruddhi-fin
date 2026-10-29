package com.digitalrockstar.samruddhifinance;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Dialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.ProgressBar;
import android.graphics.Color;

public class MainActivity extends Activity {
    private static final String HOME_URL = "https://samruddhi-fin-y8m9.onrender.com";
    private static final String APP_HOST = "samruddhi-fin-y8m9.onrender.com";
    private WebView webView;
    private ProgressBar progressBar;
    private ValueCallback<Uri[]> fileChooserCallback;
    private static final int FILE_CHOOSER_REQUEST = 4107;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(23, 27, 26));
        getWindow().setNavigationBarColor(Color.rgb(23, 27, 26));
        getWindow().getDecorView().setSystemUiVisibility(0);

        FrameLayout root = new FrameLayout(this);
        webView = new WebView(this);
        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setMax(100);
        progressBar.setIndeterminate(false);
        progressBar.setProgressTintList(android.content.res.ColorStateList.valueOf(Color.rgb(57, 118, 93)));
        progressBar.setBackgroundColor(Color.TRANSPARENT);
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(-1, dp(3));
        root.addView(progressBar, progressParams);
        setContentView(root);

        CookieManager cookieManager = CookieManager.getInstance();
        cookieManager.setAcceptCookie(true);
        cookieManager.setAcceptThirdPartyCookies(webView, true);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setLoadsImagesAutomatically(true);
        settings.setBlockNetworkImage(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        settings.setSupportZoom(false);
        settings.setBuiltInZoomControls(false);
        settings.setDisplayZoomControls(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);
        settings.setSupportMultipleWindows(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(true);

        webView.setWebViewClient(new AppWebViewClient(false, null));
        webView.setWebChromeClient(new AppChromeClient(false, null));
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(HOME_URL);
        }
    }

    private int dp(int value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private boolean isInternal(Uri uri) {
        return uri != null && "https".equalsIgnoreCase(uri.getScheme())
                && APP_HOST.equalsIgnoreCase(uri.getHost());
    }

    private void openExternal(Uri uri) {
        try {
            Intent intent = new Intent(Intent.ACTION_VIEW, uri);
            startActivity(intent);
        } catch (ActivityNotFoundException ignored) {
            new AlertDialog.Builder(this)
                    .setMessage("No app is available to open this link.")
                    .setPositiveButton("OK", null)
                    .show();
        }
    }

    private class AppWebViewClient extends WebViewClient {
        private final boolean popup;
        private final Dialog popupDialog;

        AppWebViewClient(boolean popup, Dialog popupDialog) {
            this.popup = popup;
            this.popupDialog = popupDialog;
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return routeUrl(view, request.getUrl());
        }

        @Override
        public boolean shouldOverrideUrlLoading(WebView view, String url) {
            return routeUrl(view, Uri.parse(url));
        }

        private boolean routeUrl(WebView view, Uri uri) {
            String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase();
            if (isInternal(uri)) {
                if (popup) {
                    if (popupDialog != null) popupDialog.dismiss();
                    webView.loadUrl(uri.toString());
                    return true;
                }
                return false;
            }
            if ("http".equals(scheme) || "https".equals(scheme)
                    || "mailto".equals(scheme) || "tel".equals(scheme)
                    || "sms".equals(scheme) || "geo".equals(scheme)) {
                if (popup && popupDialog != null) popupDialog.dismiss();
                openExternal(uri);
                return true;
            }
            if ("about".equals(scheme) || "blob".equals(scheme) || "data".equals(scheme)) {
                return false;
            }
            if (popup && popupDialog != null) popupDialog.dismiss();
            openExternal(uri);
            return true;
        }
    }

    private class AppChromeClient extends WebChromeClient {
        private final boolean popup;
        private final Dialog popupDialog;

        AppChromeClient(boolean popup, Dialog popupDialog) {
            this.popup = popup;
            this.popupDialog = popupDialog;
        }

        @Override
        public void onProgressChanged(WebView view, int progress) {
            if (!popup) {
                progressBar.setProgress(progress);
                progressBar.setVisibility(progress >= 100 ? View.GONE : View.VISIBLE);
            }
        }

        @Override
        public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture, android.os.Message resultMsg) {
            final Dialog dialog = new Dialog(MainActivity.this);
            WebView popupView = new WebView(MainActivity.this);
            WebSettings popupSettings = popupView.getSettings();
            popupSettings.setJavaScriptEnabled(true);
            popupSettings.setDomStorageEnabled(true);
            popupSettings.setJavaScriptCanOpenWindowsAutomatically(true);
            popupSettings.setSupportMultipleWindows(true);
            popupSettings.setUseWideViewPort(true);
            popupSettings.setLoadWithOverviewMode(true);
            popupView.setWebViewClient(new AppWebViewClient(true, dialog));
            popupView.setWebChromeClient(new AppChromeClient(true, dialog));

            FrameLayout popupRoot = new FrameLayout(MainActivity.this);
            popupRoot.addView(popupView, new FrameLayout.LayoutParams(-1, -1));
            dialog.setContentView(popupRoot);
            Window window = dialog.getWindow();
            if (window != null) {
                window.setLayout(-1, -1);
                window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
            }
            dialog.setOnDismissListener(ignored -> {
                popupView.stopLoading();
                popupView.destroy();
            });
            WebView.WebViewTransport transport = (WebView.WebViewTransport) resultMsg.obj;
            transport.setWebView(popupView);
            resultMsg.sendToTarget();
            dialog.show();
            if (dialog.getWindow() != null) dialog.getWindow().setLayout(-1, -1);
            return true;
        }

        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            if (fileChooserCallback != null) fileChooserCallback.onReceiveValue(null);
            fileChooserCallback = callback;
            Intent intent;
            try {
                intent = params.createIntent();
                startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                return true;
            } catch (ActivityNotFoundException ex) {
                fileChooserCallback = null;
                return false;
            }
        }

        @Override
        public void onCloseWindow(WebView window) {
            if (popupDialog != null) popupDialog.dismiss();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_CHOOSER_REQUEST && fileChooserCallback != null) {
            Uri[] results = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
            fileChooserCallback.onReceiveValue(results);
            fileChooserCallback = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        if (webView != null) webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    protected void onDestroy() {
        if (fileChooserCallback != null) fileChooserCallback.onReceiveValue(null);
        if (webView != null) {
            webView.stopLoading();
            webView.setWebChromeClient(null);
            webView.setWebViewClient(null);
            webView.destroy();
        }
        super.onDestroy();
    }
}
