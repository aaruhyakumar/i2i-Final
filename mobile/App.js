import React, { useState, useRef, useEffect } from 'react';
import {
  StyleSheet,
  View,
  SafeAreaView,
  StatusBar,
  ActivityIndicator,
  Text,
  TouchableOpacity,
  TextInput,
  Platform,
  Alert,
  PermissionsAndroid,
  Linking
} from 'react-native';
import { WebView } from 'react-native-webview';
import DocumentScanner, { ResponseType } from 'react-native-document-scanner-plugin';

const DEFAULT_SERVER_URL = 'https://i2i-final.onrender.com';

export default function App() {
  const [currentUrl, setCurrentUrl] = useState(DEFAULT_SERVER_URL);
  const [inputUrl, setInputUrl] = useState(DEFAULT_SERVER_URL);
  const [showConfig, setShowConfig] = useState(false);
  const [loading, setLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [statusBarHidden, setStatusBarHidden] = useState(false);
  const webViewRef = useRef(null);
  const pendingAuthUrlRef = useRef(null);
  const isWebViewReadyRef = useRef(false);

  const forwardAuthDeepLink = (url) => {
    if (!url || typeof url !== 'string') return;
    // Strictly restrict to approved ink2insight auth callback scheme/host
    if (!url.startsWith('ink2insight://auth/callback') && !url.startsWith('ink2insight://auth')) {
      return;
    }
    if (isWebViewReadyRef.current) {
      sendToWebView({
        type: 'AUTH_DEEP_LINK',
        url: url
      });
    } else {
      pendingAuthUrlRef.current = url;
    }
  };

  useEffect(() => {
    // Hide status bar during initial intro window fallback
    setStatusBarHidden(true);
    const timer = setTimeout(() => {
      setStatusBarHidden(false);
    }, 11000);

    // 1. Cold start: check initial deep-link URL that launched the app
    Linking.getInitialURL().then((url) => {
      if (url) {
        forwardAuthDeepLink(url);
      }
    }).catch((err) => {
      console.warn('Failed to retrieve initial deep link URL:', err);
    });

    // 2. Warm start: listen for runtime deep-links while app is open or backgrounded
    const subscription = Linking.addEventListener('url', (event) => {
      if (event && event.url) {
        forwardAuthDeepLink(event.url);
      }
    });

    return () => {
      clearTimeout(timer);
      if (subscription && typeof subscription.remove === 'function') {
        subscription.remove();
      }
    };
  }, []);

  const sendToWebView = (payload) => {
    if (webViewRef.current) {
      try {
        const jsonStr = JSON.stringify(payload);
        // Single, reliable communication mechanism via postMessage
        webViewRef.current.postMessage(jsonStr);
      } catch (err) {
        console.error('Failed to send payload to WebView:', err);
      }
    }
  };

  const requestCameraPermission = async () => {
    if (Platform.OS === 'android') {
      try {
        const checkResult = await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.CAMERA);
        if (checkResult) {
          return true;
        }
        const granted = await PermissionsAndroid.request(
          PermissionsAndroid.PERMISSIONS.CAMERA,
          {
            title: "Camera Access Required",
            message: "Ink2Insights needs camera permission to scan clinical intake forms with ML Kit.",
            buttonNeutral: "Ask Me Later",
            buttonNegative: "Cancel",
            buttonPositive: "OK"
          }
        );
        return granted === PermissionsAndroid.RESULTS.GRANTED;
      } catch (err) {
        console.warn('Camera permission check error:', err);
        return false;
      }
    }
    return true;
  };

  const handleNativeScan = async (data) => {
    try {
      const hasPermission = await requestCameraPermission();
      if (!hasPermission) {
        sendToWebView({
          type: 'NATIVE_SCAN_ERROR',
          scanId: Date.now(),
          error: 'Camera permission was denied. Please allow camera access in Android Settings.',
          targetPageIndex: (data && data.targetPageIndex !== undefined) ? data.targetPageIndex : null
        });
        return;
      }

      const maxDocs = (data && typeof data.maxNumDocuments === 'number') ? data.maxNumDocuments : 2;
      const targetPageIndex = (data && data.targetPageIndex !== undefined) ? data.targetPageIndex : null;

      const scanResult = await DocumentScanner.scanDocument({
        maxNumDocuments: maxDocs,
        croppedImageQuality: 90,
        responseType: ResponseType.Base64,
      });

      const scanId = Date.now();
      if (scanResult && scanResult.status === 'success' && scanResult.scannedImages && scanResult.scannedImages.length > 0) {
        sendToWebView({
          type: 'NATIVE_SCAN_SUCCESS',
          scanId,
          images: scanResult.scannedImages,
          targetPageIndex: targetPageIndex
        });
      } else {
        // User cancelled or closed the native scanner
        sendToWebView({
          type: 'NATIVE_SCAN_CANCELLED',
          scanId,
          targetPageIndex: targetPageIndex
        });
      }
    } catch (err) {
      console.warn('DocumentScanner execution error:', err);
      sendToWebView({
        type: 'NATIVE_SCAN_ERROR',
        scanId: Date.now(),
        error: err.message || 'Document scanning failed',
        targetPageIndex: (data && data.targetPageIndex !== undefined) ? data.targetPageIndex : null
      });
    }
  };

  const handleMessage = (event) => {
    try {
      const data = JSON.parse(event.nativeEvent.data);
      if (data.type === 'intro-start' || data.type === 'intro:start') {
        setStatusBarHidden(true);
      } else if (data.type === 'intro-end' || data.type === 'intro:end') {
        setStatusBarHidden(false);
      } else if (data.type === 'START_NATIVE_SCAN') {
        handleNativeScan(data);
      } else if (data.type === 'WEBVIEW_READY') {
        isWebViewReadyRef.current = true;
        if (pendingAuthUrlRef.current) {
          const authUrl = pendingAuthUrlRef.current;
          pendingAuthUrlRef.current = null;
          sendToWebView({
            type: 'AUTH_DEEP_LINK',
            url: authUrl
          });
        }
      }
    } catch (e) {
      console.warn('WebView onMessage error:', e);
    }
  };

  const handleReload = () => {
    setHasError(false);
    setLoading(true);
    if (webViewRef.current) {
      webViewRef.current.reload();
    }
  };

  const handleSaveUrl = () => {
    let formatted = inputUrl.trim();
    if (!formatted.startsWith('http://') && !formatted.startsWith('https://')) {
      formatted = 'http://' + formatted;
    }
    setCurrentUrl(formatted);
    setShowConfig(false);
    setHasError(false);
    setLoading(true);
  };

  return (
    <SafeAreaView style={[styles.safeArea, statusBarHidden && styles.safeAreaFullscreen]}>
      <StatusBar hidden={statusBarHidden} translucent backgroundColor="transparent" barStyle="light-content" />

      {/* Top Floating Control Bar - hidden during full-screen brand intro */}
      {!statusBarHidden && (
        <View style={styles.topBar}>
          <View style={styles.statusIndicator}>
            <View style={[styles.statusDot, { backgroundColor: hasError ? '#EF4444' : '#10B981' }]} />
            <Text style={styles.statusText} numberOfLines={1}>
              {currentUrl}
            </Text>
          </View>

          <View style={styles.buttonGroup}>
            <TouchableOpacity style={styles.iconButton} onPress={handleReload}>
              <Text style={styles.iconButtonText}>🔄</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.configButton} onPress={() => setShowConfig(!showConfig)}>
              <Text style={styles.configButtonText}>⚙️ IP</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* IP Configuration Banner Modal */}
      {showConfig && (
        <View style={styles.configCard}>
          <Text style={styles.configTitle}>Backend Server Address</Text>
          <Text style={styles.configSubtitle}>
            Ensure your phone and PC are connected to the same Wi-Fi.
          </Text>
          <TextInput
            style={styles.input}
            value={inputUrl}
            onChangeText={setInputUrl}
            placeholder="http://192.168.1.7:5001"
            placeholderTextColor="#94A3B8"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <View style={styles.configActionRow}>
            <TouchableOpacity
              style={[styles.actionBtn, styles.cancelBtn]}
              onPress={() => setShowConfig(false)}
            >
              <Text style={styles.actionBtnText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.actionBtn, styles.saveBtn]}
              onPress={handleSaveUrl}
            >
              <Text style={[styles.actionBtnText, { color: '#FFFFFF', fontWeight: 'bold' }]}>
                Connect
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* Main WebView Container */}
      <View style={styles.webContainer}>
        <WebView
          ref={webViewRef}
          source={{ uri: currentUrl }}
          style={styles.webView}
          webviewDebuggingEnabled={true}
          javaScriptEnabled={true}
          domStorageEnabled={true}
          startInLoadingState={true}
          scalesPageToFit={true}
          allowsInlineMediaPlayback={true}
          mediaPlaybackRequiresUserAction={false}
          onMessage={handleMessage}
          allowsBackForwardNavigationGestures={true}
          allowFileAccess={true}
          allowFileAccessFromFileURLs={false}
          allowUniversalAccessFromFileURLs={false}
          originWhitelist={['*']}
          mixedContentMode="always"
          cacheEnabled={false}
          cacheMode="LOAD_NO_CACHE"
          userAgent="Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36"
          cameraPermissionExpires={Date.now() + 3600000}
          onOpenFileChooser={() => true}
          onShouldStartLoadWithRequest={(request) => {
            return true;
          }}
          geolocationEnabled={true}
          setBuiltInZoomControls={false}
          setDisplayZoomControls={false}
          onLoadStart={() => {
            setLoading(true);
            setHasError(false);
          }}
          onLoadEnd={() => {
            setLoading(false);
            isWebViewReadyRef.current = true;
            if (pendingAuthUrlRef.current) {
              const authUrl = pendingAuthUrlRef.current;
              pendingAuthUrlRef.current = null;
              setTimeout(() => {
                sendToWebView({
                  type: 'AUTH_DEEP_LINK',
                  url: authUrl
                });
              }, 400);
            }
          }}
          onError={(syntheticEvent) => {
            const { nativeEvent } = syntheticEvent;
            setLoading(false);
            setHasError(true);
            Alert.alert(
              "Connection Issue",
              `Cannot reach ${currentUrl}.\n\nCheck that:\n1. Your phone is on the same Wi-Fi.\n2. Flask server is running.\n3. Tap ⚙️ IP above if your computer's IP changed.`,
              [{ text: "OK" }]
            );
          }}
        />

        {loading && (
          <View style={styles.loaderOverlay}>
            <ActivityIndicator size="large" color="#0284C7" />
            <Text style={styles.loaderText}>Connecting to Ink 2 Insight...</Text>
          </View>
        )}

        {hasError && (
          <View style={styles.errorOverlay}>
            <Text style={styles.errorIcon}>📡</Text>
            <Text style={styles.errorTitle}>Cannot Connect to Server</Text>
            <Text style={styles.errorDetails}>
              Could not reach {currentUrl}
            </Text>
            <TouchableOpacity style={styles.retryBtn} onPress={handleReload}>
              <Text style={styles.retryBtnText}>Retry Connection</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.retryBtn, { backgroundColor: '#334155', marginTop: 10 }]}
              onPress={() => setShowConfig(true)}
            >
              <Text style={styles.retryBtnText}>Change IP Address</Text>
            </TouchableOpacity>
          </View>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#0F172A',
    paddingTop: Platform.OS === 'android' ? StatusBar.currentHeight : 0,
  },
  safeAreaFullscreen: {
    paddingTop: 0,
    backgroundColor: '#000000',
  },
  topBar: {
    height: 44,
    backgroundColor: '#0F172A',
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 12,
    borderBottomWidth: 1,
    borderBottomColor: '#1E293B',
  },
  statusIndicator: {
    flexDirection: 'row',
    alignItems: 'center',
    flex: 1,
    marginRight: 8,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 8,
  },
  statusText: {
    color: '#94A3B8',
    fontSize: 12,
    fontWeight: '600',
  },
  buttonGroup: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  iconButton: {
    padding: 6,
    marginRight: 6,
  },
  iconButtonText: {
    fontSize: 14,
  },
  configButton: {
    backgroundColor: '#1E293B',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: '#334155',
  },
  configButtonText: {
    color: '#38BDF8',
    fontSize: 11,
    fontWeight: '700',
  },
  configCard: {
    backgroundColor: '#1E293B',
    margin: 12,
    padding: 14,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#38BDF8',
    elevation: 6,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 6,
  },
  configTitle: {
    color: '#F8FAFC',
    fontSize: 14,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  configSubtitle: {
    color: '#94A3B8',
    fontSize: 11,
    marginBottom: 10,
  },
  input: {
    backgroundColor: '#0F172A',
    color: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#334155',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 13,
    marginBottom: 12,
  },
  configActionRow: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: 8,
  },
  actionBtn: {
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 6,
  },
  cancelBtn: {
    backgroundColor: '#334155',
  },
  saveBtn: {
    backgroundColor: '#0284C7',
  },
  actionBtnText: {
    color: '#CBD5E1',
    fontSize: 12,
    fontWeight: '600',
  },
  webContainer: {
    flex: 1,
    backgroundColor: '#0F172A',
    position: 'relative',
  },
  webView: {
    flex: 1,
    backgroundColor: '#0F172A',
  },
  loaderOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10,
  },
  loaderText: {
    color: '#94A3B8',
    fontSize: 13,
    marginTop: 12,
    fontWeight: '500',
  },
  errorOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: '#0F172A',
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
    zIndex: 20,
  },
  errorIcon: {
    fontSize: 48,
    marginBottom: 12,
  },
  errorTitle: {
    color: '#F8FAFC',
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 8,
  },
  errorDetails: {
    color: '#94A3B8',
    fontSize: 13,
    textAlign: 'center',
    marginBottom: 20,
  },
  retryBtn: {
    backgroundColor: '#0284C7',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
    minWidth: 160,
    alignItems: 'center',
  },
  retryBtnText: {
    color: '#FFFFFF',
    fontWeight: 'bold',
    fontSize: 14,
  },
});
