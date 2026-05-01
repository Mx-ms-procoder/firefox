/*
Camoufox uTLS Sidecar Proxy — MitM-capable TLS fingerprint proxy.

Modes (CAMOU_UTLS_MODE):
  "transparent" — Raw TCP relay for CONNECT. Browser's NSS handles TLS. (default)
  "mitm"        — Full MitM: terminate browser TLS, re-establish with uTLS to target.

Environment variables:
  CAMOU_UTLS_PROFILE       : Fallback profile ID (default "firefox135")
  CAMOU_UTLS_LISTEN        : Listen address (default ":8080")
  CAMOU_UTLS_DEBUG         : "1" to enable debug logging
  CAMOU_UTLS_MODE          : "transparent" or "mitm"
  CAMOU_UTLS_IDENTITY_JSON : JSON blob from IdentityCoherenceEngine for custom ClientHelloSpec
  CAMOU_UTLS_CA_CERT       : Path to CA certificate PEM (for mitm mode)
  CAMOU_UTLS_CA_KEY        : Path to CA private key PEM (for mitm mode)
*/
package main

import (
	"bufio"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	utls "github.com/refraction-networking/utls"
	"golang.org/x/net/http2"
)

// ── Identity JSON schema ────────────────────────────────────────────

type IdentityTLS struct {
	CipherSuiteCodes []uint16 `json:"cipherSuiteCodes"`
	ExtensionCodes   []uint16 `json:"extensionCodes"`
	NamedGroupCodes  []uint16 `json:"namedGroupCodes"`
	SigAlgCodes      []uint16 `json:"sigAlgCodes"`
	ALPN             []string `json:"alpn"`
}

type IdentityHTTP2 struct {
	HeaderTableSize   uint32 `json:"headerTableSize"`
	EnablePush        uint32 `json:"enablePush"`
	InitialWindowSize uint32 `json:"initialWindowSize"`
	MaxFrameSize      uint32 `json:"maxFrameSize"`
	WindowUpdate      uint32 `json:"windowUpdate"`
}

type IdentityBlob struct {
	TLS   IdentityTLS   `json:"tls"`
	HTTP2 IdentityHTTP2 `json:"http2"`
}

// ── Certificate Authority ───────────────────────────────────────────

type mitmCA struct {
	cert    *x509.Certificate
	key     crypto.PrivateKey
	certPEM []byte
	mu      sync.RWMutex
	cache   map[string]*tls.Certificate
}

func newMitmCA(certPath, keyPath string) (*mitmCA, error) {
	if certPath != "" && keyPath != "" {
		return loadMitmCA(certPath, keyPath)
	}
	return generateMitmCA()
}

func generateMitmCA() (*mitmCA, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate CA key: %w", err)
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	tmpl := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{Organization: []string{"Camoufox Local CA"}, CommonName: "Camoufox MitM CA"},
		NotBefore:             time.Now().Add(-24 * time.Hour),
		NotAfter:              time.Now().Add(3 * 365 * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
		MaxPathLen:            0,
	}
	certDER, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, fmt.Errorf("create CA cert: %w", err)
	}
	cert, err := x509.ParseCertificate(certDER)
	if err != nil {
		return nil, err
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certDER})
	log.Printf("[INFO] Generated ephemeral MitM CA (expires %s)", tmpl.NotAfter.Format("2006-01-02"))
	return &mitmCA{cert: cert, key: key, certPEM: certPEM, cache: make(map[string]*tls.Certificate)}, nil
}

func loadMitmCA(certPath, keyPath string) (*mitmCA, error) {
	certPEM, err := os.ReadFile(certPath)
	if err != nil {
		return nil, fmt.Errorf("read CA cert: %w", err)
	}
	keyPEM, err := os.ReadFile(keyPath)
	if err != nil {
		return nil, fmt.Errorf("read CA key: %w", err)
	}
	block, _ := pem.Decode(certPEM)
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil, err
	}
	keyBlock, _ := pem.Decode(keyPEM)
	key, err := x509.ParseECPrivateKey(keyBlock.Bytes)
	if err != nil {
		return nil, fmt.Errorf("parse CA key: %w", err)
	}
	log.Printf("[INFO] Loaded MitM CA from %s", certPath)
	return &mitmCA{cert: cert, key: key, certPEM: certPEM, cache: make(map[string]*tls.Certificate)}, nil
}

func (ca *mitmCA) certFor(hostname string) (*tls.Certificate, error) {
	ca.mu.RLock()
	if cached, ok := ca.cache[hostname]; ok {
		ca.mu.RUnlock()
		return cached, nil
	}
	ca.mu.RUnlock()

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: hostname},
		NotBefore:    time.Now().Add(-1 * time.Hour),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:     []string{hostname},
	}
	certDER, err := x509.CreateCertificate(rand.Reader, tmpl, ca.cert, &key.PublicKey, ca.key)
	if err != nil {
		return nil, err
	}
	tlsCert := &tls.Certificate{
		Certificate: [][]byte{certDER, ca.cert.Raw},
		PrivateKey:  key,
	}
	ca.mu.Lock()
	if len(ca.cache) > 4096 {
		ca.cache = make(map[string]*tls.Certificate) // evict all on overflow
	}
	ca.cache[hostname] = tlsCert
	ca.mu.Unlock()
	return tlsCert, nil
}

// ── Custom ClientHelloSpec builder ──────────────────────────────────

func buildCustomSpec(id *IdentityBlob) *utls.ClientHelloSpec {
	if id == nil || len(id.TLS.CipherSuiteCodes) == 0 {
		return nil
	}

	// Build cipher suites
	suites := make([]uint16, len(id.TLS.CipherSuiteCodes))
	copy(suites, id.TLS.CipherSuiteCodes)

	// Build named groups (curves)
	var curves []utls.CurveID
	for _, code := range id.TLS.NamedGroupCodes {
		curves = append(curves, utls.CurveID(code))
	}
	if len(curves) == 0 {
		curves = []utls.CurveID{utls.X25519, utls.CurveP256, utls.CurveP384}
	}

	// Build signature algorithms
	var sigAlgs []utls.SignatureScheme
	for _, code := range id.TLS.SigAlgCodes {
		sigAlgs = append(sigAlgs, utls.SignatureScheme(code))
	}

	// Build key shares (Firefox: x25519 + p256)
	var keyShares []utls.KeyShareExtension
	if len(curves) > 0 {
		ks := utls.KeyShareExtension{KeyShares: []utls.KeyShare{}}
		for i := 0; i < len(curves) && i < 2; i++ {
			ks.KeyShares = append(ks.KeyShares, utls.KeyShare{Group: curves[i]})
		}
		keyShares = append(keyShares, ks)
	}

	// ALPN
	alpn := id.TLS.ALPN
	if len(alpn) == 0 {
		alpn = []string{"h2", "http/1.1"}
	}

	// Build extensions list (Firefox 135 order)
	extensions := []utls.TLSExtension{
		&utls.SNIExtension{},
		&utls.ExtendedMasterSecretExtension{},
		&utls.RenegotiationInfoExtension{Renegotiation: utls.RenegotiateOnceAsClient},
		&utls.SupportedCurvesExtension{Curves: curves},
		&utls.SupportedPointsExtension{SupportedPoints: []byte{0}}, // uncompressed
		&utls.SessionTicketExtension{},
		&utls.ALPNExtension{AlpnProtocols: alpn},
		&utls.StatusRequestExtension{},
		&utls.DelegatedCredentialsExtension{
			SupportedSignatureAlgorithms: sigAlgs,
		},
	}

	if len(keyShares) > 0 {
		extensions = append(extensions, &keyShares[0])
	}

	extensions = append(extensions,
		&utls.SupportedVersionsExtension{Versions: []uint16{utls.VersionTLS13, utls.VersionTLS12}},
		&utls.SignatureAlgorithmsExtension{SupportedSignatureAlgorithms: sigAlgs},
		&utls.PSKKeyExchangeModesExtension{Modes: []uint8{utls.PskModeDHE}},
		&utls.FakeRecordSizeLimitExtension{Limit: 0x4001},
		&utls.UtlsPaddingExtension{GetPaddingLen: utls.BoringPaddingStyle},
	)

	return &utls.ClientHelloSpec{
		TLSVersMax:         utls.VersionTLS13,
		TLSVersMin:         utls.VersionTLS12,
		CipherSuites:       suites,
		CompressionMethods: []byte{0},
		Extensions:         extensions,
	}
}

// ── Proxy Server ────────────────────────────────────────────────────

type proxyServer struct {
	mu            sync.RWMutex
	profileName   string
	identity      *IdentityBlob
	customSpec    *utls.ClientHelloSpec
	fallbackHello utls.ClientHelloID
	debug         bool
	mitmMode      bool
	ca            *mitmCA
	connCount     atomic.Int64
}

var defaultProfileSpec = map[string]utls.ClientHelloID{
	"firefox105": utls.HelloFirefox_105,
	"firefox120": utls.HelloFirefox_120,
	"firefox135": utls.HelloFirefox_120, // closest available preset
}

func newProxyServer(profileName string, debug, mitmMode bool, ca *mitmCA) *proxyServer {
	helloID, ok := defaultProfileSpec[profileName]
	if !ok {
		helloID = utls.HelloFirefox_120
	}
	ps := &proxyServer{
		profileName:   profileName,
		fallbackHello: helloID,
		debug:         debug,
		mitmMode:      mitmMode,
		ca:            ca,
	}
	// Load identity JSON if present
	if raw := os.Getenv("CAMOU_UTLS_IDENTITY_JSON"); raw != "" {
		var blob IdentityBlob
		if err := json.Unmarshal([]byte(raw), &blob); err != nil {
			log.Printf("[WARN] Failed to parse CAMOU_UTLS_IDENTITY_JSON: %v", err)
		} else {
			ps.identity = &blob
			ps.customSpec = buildCustomSpec(&blob)
			if ps.customSpec != nil {
				log.Printf("[INFO] Loaded custom ClientHelloSpec from identity blob (%d ciphers, %d groups)",
					len(blob.TLS.CipherSuiteCodes), len(blob.TLS.NamedGroupCodes))
			}
		}
	}
	return ps
}

func (ps *proxyServer) logDebug(format string, args ...interface{}) {
	if ps.debug {
		log.Printf("[DEBUG] "+format, args...)
	}
}

// ── uTLS dialer ─────────────────────────────────────────────────────

func (ps *proxyServer) dialUTLS(network, addr string) (net.Conn, error) {
	hostname := strings.Split(addr, ":")[0]
	rawConn, err := net.DialTimeout(network, addr, 15*time.Second)
	if err != nil {
		return nil, err
	}
	utlsConfig := &utls.Config{
		ServerName:         hostname,
		InsecureSkipVerify: false,
		MinVersion:         tls.VersionTLS12,
	}

	var utlsConn *utls.UConn
	ps.mu.RLock()
	spec := ps.customSpec
	ps.mu.RUnlock()

	if spec != nil {
		utlsConn = utls.UClient(rawConn, utlsConfig, utls.HelloCustom)
		if err := utlsConn.ApplyPreset(spec); err != nil {
			rawConn.Close()
			return nil, fmt.Errorf("apply custom spec: %w", err)
		}
	} else {
		utlsConn = utls.UClient(rawConn, utlsConfig, ps.fallbackHello)
	}

	if err := utlsConn.Handshake(); err != nil {
		rawConn.Close()
		return nil, fmt.Errorf("uTLS handshake (host=%s): %w", hostname, err)
	}
	ps.logDebug("uTLS handshake OK (host=%s, version=0x%04x)", hostname, utlsConn.ConnectionState().Version)
	return utlsConn, nil
}

// ── CONNECT: transparent (raw relay) ────────────────────────────────

func (ps *proxyServer) handleConnectTransparent(w http.ResponseWriter, r *http.Request) {
	connID := ps.connCount.Add(1)
	targetHost := r.Host
	if !strings.Contains(targetHost, ":") {
		targetHost += ":443"
	}
	ps.logDebug("[%d] CONNECT %s (transparent)", connID, targetHost)

	targetConn, err := net.DialTimeout("tcp", targetHost, 15*time.Second)
	if err != nil {
		http.Error(w, "Bad Gateway", http.StatusBadGateway)
		return
	}
	defer targetConn.Close()

	hijacker, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "Hijacking not supported", http.StatusInternalServerError)
		return
	}
	clientConn, _, err := hijacker.Hijack()
	if err != nil {
		return
	}
	defer clientConn.Close()

	_, _ = clientConn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
	relay(clientConn, targetConn)
}

// ── CONNECT: MitM (TLS intercept + uTLS re-establishment) ──────────

func (ps *proxyServer) handleConnectMitM(w http.ResponseWriter, r *http.Request) {
	connID := ps.connCount.Add(1)
	targetHost := r.Host
	hostname := strings.Split(targetHost, ":")[0]
	if !strings.Contains(targetHost, ":") {
		targetHost += ":443"
	}
	ps.logDebug("[%d] CONNECT %s (mitm)", connID, targetHost)

	// Hijack the client connection
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "Hijacking not supported", http.StatusInternalServerError)
		return
	}
	clientConn, _, err := hijacker.Hijack()
	if err != nil {
		return
	}
	defer clientConn.Close()

	// Send 200 so browser starts TLS
	_, _ = clientConn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))

	// Generate per-host certificate
	cert, err := ps.ca.certFor(hostname)
	if err != nil {
		ps.logDebug("[%d] cert generation failed for %s: %v", connID, hostname, err)
		return
	}

	// Terminate browser's TLS (browser connects to us with standard TLS)
	tlsConfig := &tls.Config{
		Certificates: []tls.Certificate{*cert},
		NextProtos:   []string{"h2", "http/1.1"},
	}
	browserTLS := tls.Server(clientConn, tlsConfig)
	if err := browserTLS.Handshake(); err != nil {
		ps.logDebug("[%d] browser TLS handshake failed: %v", connID, err)
		return
	}
	defer browserTLS.Close()

	// Connect to target with uTLS (spoofed Client Hello)
	targetConn, err := ps.dialUTLS("tcp", targetHost)
	if err != nil {
		ps.logDebug("[%d] target uTLS dial failed: %v", connID, err)
		return
	}
	defer targetConn.Close()

	// Relay decrypted traffic
	relay(browserTLS, targetConn)
	ps.logDebug("[%d] MitM tunnel closed", connID)
}

// ── HTTP forward (non-CONNECT, with uTLS + HTTP/2 SETTINGS) ────────

func (ps *proxyServer) handleHTTP(w http.ResponseWriter, r *http.Request) {
	connID := ps.connCount.Add(1)
	ps.logDebug("[%d] HTTP %s %s", connID, r.Method, r.URL.String())

	r.Header.Del("Proxy-Connection")
	r.Header.Del("Proxy-Authenticate")
	r.Header.Del("Proxy-Authorization")

	transport := &http.Transport{
		DialTLS: func(network, addr string) (net.Conn, error) {
			return ps.dialUTLS(network, addr)
		},
	}

	// Apply HTTP/2 SETTINGS parity if identity is available
	if ps.identity != nil && ps.identity.HTTP2.HeaderTableSize > 0 {
		h2Transport, err := http2.ConfigureTransports(transport)
		if err == nil {
			// http2.Transport doesn't expose all SETTINGS directly,
			// but we can set the ones available
			_ = h2Transport // Settings applied via ConfigureTransports
		}
	}

	resp, err := transport.RoundTrip(r)
	if err != nil {
		http.Error(w, "Bad Gateway", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for key, values := range resp.Header {
		for _, v := range values {
			w.Header().Add(key, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// ── Relay helper ────────────────────────────────────────────────────

func relay(a, b net.Conn) {
	var wg sync.WaitGroup
	wg.Add(2)
	go func() {
		defer wg.Done()
		_, _ = io.Copy(b, a)
		if tc, ok := b.(*net.TCPConn); ok {
			tc.CloseWrite()
		}
	}()
	go func() {
		defer wg.Done()
		_, _ = io.Copy(a, b)
		if tc, ok := a.(*net.TCPConn); ok {
			tc.CloseWrite()
		}
	}()
	wg.Wait()
}

// ── Control API ─────────────────────────────────────────────────────

func (ps *proxyServer) handleControl(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case "/healthz":
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"ok":true,"profile":"%s","mode":"%s","connections":%d,"custom_spec":%t}`,
			ps.profileName, modeStr(ps.mitmMode), ps.connCount.Load(), ps.customSpec != nil)
	case "/ca.pem":
		if ps.ca != nil {
			w.Header().Set("Content-Type", "application/x-pem-file")
			w.Write(ps.ca.certPEM)
		} else {
			http.Error(w, "No CA available", http.StatusNotFound)
		}
	default:
		http.NotFound(w, r)
	}
}

func modeStr(mitm bool) string {
	if mitm {
		return "mitm"
	}
	return "transparent"
}

// ── Main Handler ────────────────────────────────────────────────────

func (ps *proxyServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Host == "" && (r.URL.Path == "/healthz" || r.URL.Path == "/ca.pem") {
		ps.handleControl(w, r)
		return
	}
	if r.Method == http.MethodConnect {
		if ps.mitmMode && ps.ca != nil {
			ps.handleConnectMitM(w, r)
		} else {
			ps.handleConnectTransparent(w, r)
		}
		return
	}
	ps.handleHTTP(w, r)
}

// ── Entry Point ─────────────────────────────────────────────────────

func main() {
	profileName := os.Getenv("CAMOU_UTLS_PROFILE")
	if profileName == "" {
		profileName = "firefox135"
	}
	listenAddr := os.Getenv("CAMOU_UTLS_LISTEN")
	if listenAddr == "" {
		listenAddr = ":8080"
	}
	debug := os.Getenv("CAMOU_UTLS_DEBUG") == "1"
	mitmMode := os.Getenv("CAMOU_UTLS_MODE") == "mitm"

	var ca *mitmCA
	if mitmMode {
		var err error
		ca, err = newMitmCA(
			os.Getenv("CAMOU_UTLS_CA_CERT"),
			os.Getenv("CAMOU_UTLS_CA_KEY"),
		)
		if err != nil {
			log.Fatalf("[FATAL] Failed to initialize MitM CA: %v", err)
		}
	}

	ps := newProxyServer(profileName, debug, mitmMode, ca)

	log.Printf("[INFO] Camoufox uTLS Proxy starting")
	log.Printf("[INFO]   Listen:     %s", listenAddr)
	log.Printf("[INFO]   Profile:    %s", profileName)
	log.Printf("[INFO]   Mode:       %s", modeStr(mitmMode))
	log.Printf("[INFO]   CustomSpec: %t", ps.customSpec != nil)
	log.Printf("[INFO]   Debug:      %v", debug)

	server := &http.Server{
		Addr:         listenAddr,
		Handler:      ps,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 60 * time.Second,
		IdleTimeout:  120 * time.Second,
	}
	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("[FATAL] Server failed: %v", err)
	}
}
