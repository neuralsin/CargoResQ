import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as Location from "expo-location";
import * as SecureStore from "expo-secure-store";
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { ApiError, ActiveIncident, DriverProfile, getDriver, getIncident, getShipment, login, reportBreakdown, Shipment } from "./src/api";

const C = {
  ink: "#101615",
  muted: "#7B8683",
  canvas: "#F4F7F5",
  card: "#FFFFFF",
  line: "#E1E9E5",
  teal: "#0F766E",
  mint: "#E4F3EF",
  amber: "#B7791F",
  amberSoft: "#FFF3D6",
  red: "#B42318",
  redSoft: "#FDEBE8"
};

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);
  useEffect(() => {
    SecureStore.getItemAsync("cargoresq_driver_token").then((value) => {
      setToken(value);
      setBooting(false);
    });
  }, []);
  if (booting) return <Centered><ActivityIndicator color={C.teal} /></Centered>;
  return token ? (
    <DriverHome
      token={token}
      onSignOut={async () => {
        await SecureStore.deleteItemAsync("cargoresq_driver_token");
        setToken(null);
      }}
    />
  ) : (
    <LoginScreen
      onLoggedIn={async (next) => {
        await SecureStore.setItemAsync("cargoresq_driver_token", next);
        setToken(next);
      }}
    />
  );
}

function LoginScreen({ onLoggedIn }: { onLoggedIn: (token: string) => void }) {
  const [endpoint, setEndpoint] = useState(getApiBaseUrl());
  const [email, setEmail] = useState("rajesh@apexpharma.com");
  const [password, setPassword] = useState("Password123!");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (customEmail?: string, customPass?: string) => {
    const targetEmail = customEmail || email;
    const targetPass = customPass || password;
    if (!targetEmail || !targetPass) return setError("Enter your driver email and password.");
    setBusy(true);
    setError("");
    try {
      setApiBaseUrl(endpoint.trim());
      const result = await login(targetEmail.trim(), targetPass);
      onLoggedIn(result.access_token);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  };

  const demoLogin = () => {
    setEmail("rajesh@apexpharma.com");
    setPassword("Password123!");
    submit("rajesh@apexpharma.com", "Password123!");
  };

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar barStyle="dark-content" />
      <ScrollView contentContainerStyle={styles.loginWrap} keyboardShouldPersistTaps="handled">
        <View style={styles.brandMark}>
          <MaterialCommunityIcons name="truck-fast-outline" size={26} color="#FFF" />
        </View>
        <Text style={styles.brand}>CargoResQ</Text>
        <Text style={styles.eyebrow}>DRIVER COMPANION</Text>
        <Text style={styles.loginTitle}>Move the rescue forward.</Text>
        <Text style={styles.subtitle}>Securely report breakdowns, monitor cold chain in real-time, and verify pallet handover.</Text>

        <View style={styles.formCard}>
          <Text style={styles.label}>API Endpoint</Text>
          <TextInput
            autoCapitalize="none"
            value={endpoint}
            onChangeText={setEndpoint}
            placeholder="http://10.0.2.2:8000"
            placeholderTextColor="#A6B0AD"
            style={styles.input}
          />

          <Text style={styles.label}>Driver email</Text>
          <TextInput
            autoCapitalize="none"
            keyboardType="email-address"
            value={email}
            onChangeText={setEmail}
            placeholder="you@carrier.com"
            placeholderTextColor="#A6B0AD"
            style={styles.input}
          />

          <Text style={styles.label}>Password</Text>
          <TextInput
            secureTextEntry
            value={password}
            onChangeText={setPassword}
            placeholder="••••••••"
            placeholderTextColor="#A6B0AD"
            style={styles.input}
          />

          {!!error && <Text style={styles.error}>{error}</Text>}

          <Pressable style={[styles.primaryButton, busy && { opacity: 0.6 }]} onPress={() => submit()} disabled={busy}>
            {busy ? <ActivityIndicator color="#FFF" /> : <Text style={styles.primaryText}>Sign in securely</Text>}
          </Pressable>

          <Pressable style={styles.demoButton} onPress={demoLogin} disabled={busy}>
            <Text style={styles.demoButtonText}>One-Click Demo Login (Rajesh Kumar)</Text>
          </Pressable>
        </View>
        <Text style={styles.footerNote}>Your carrier administrator provisions driver accounts and assigns your rescue truck.</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function DriverHome({ token, onSignOut }: { token: string; onSignOut: () => void }) {
  const [profile, setProfile] = useState<DriverProfile | null>(null);
  const [shipment, setShipment] = useState<Shipment | null>(null);
  const [incident, setIncident] = useState<ActiveIncident>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const load = async () => {
    setRefreshing(true); setError("");
    try {
      const [driver, activeShipment, activeIncident] = await Promise.all([getDriver(token), getShipment(token), getIncident(token)]);
      setProfile(driver); setShipment(activeShipment.shipment); setIncident(activeIncident.incident);
    } catch (e) { setError(e instanceof ApiError ? e.message : "Could not refresh the driver console."); }
    finally { setRefreshing(false); }
  };
  useEffect(() => { load(); const timer = setInterval(load, 15000); return () => clearInterval(timer); }, []);
  const sendSos = async () => {
    if (!shipment) return setNotice("No active shipment is assigned to this truck.");
    Alert.alert("Report breakdown?", "CargoResQ will share your current position with the operations team.", [
      { text: "Cancel", style: "cancel" },
      { text: "Report now", style: "destructive", onPress: async () => {
        try {
          const permission = await Location.requestForegroundPermissionsAsync();
          if (permission.status !== Location.PermissionStatus.GRANTED) return setNotice("Location access is required to report a precise rescue position.");
          const location = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
          const result = await reportBreakdown(token, { shipment_id: shipment.id, lat: location.coords.latitude, lng: location.coords.longitude });
          setNotice(`Rescue request ${result.status.replaceAll("_", " ")}. Operations has been notified.`); await load();
        } catch (e) { setNotice(e instanceof ApiError ? e.message : "SOS could not be sent."); }
      }}
    ]);
  };
  const state = incident?.state || (shipment?.status === "breakdown_reported" ? "BREAKDOWN_REPORTED" : "IN_TRANSIT");
  return <SafeAreaView style={styles.safe}><StatusBar barStyle="dark-content" /><ScrollView refreshControl={<RefreshControl refreshing={refreshing} onRefresh={load} tintColor={C.teal} />} contentContainerStyle={styles.homeWrap}>
    <View style={styles.topbar}><View><Text style={styles.brandSmall}>CargoResQ</Text><Text style={styles.eyebrow}>DRIVER CONSOLE</Text></View><Pressable onPress={onSignOut} style={styles.avatar}><Text style={styles.avatarText}>{profile?.name?.slice(0, 2).toUpperCase() || "DR"}</Text></Pressable></View>
    <View style={styles.greeting}><View><Text style={styles.title}>Good to see you, {profile?.name?.split(" ")[0] || "driver"}.</Text><Text style={styles.subtitle}>Your rescue corridor is monitored live.</Text></View><View style={styles.online}><View style={styles.dot} /><Text style={styles.onlineText}>ONLINE</Text></View></View>
    {!!error && <View style={styles.errorBox}><Text style={styles.error}>{error}</Text></View>}
    {!!notice && <View style={styles.notice}><MaterialCommunityIcons name="check-circle-outline" size={18} color={C.teal} /><Text style={styles.noticeText}>{notice}</Text></View>}
    <View style={styles.card}><View style={styles.cardHeader}><View><Text style={styles.cardKicker}>ASSIGNED TRUCK</Text><Text style={styles.cardTitle}>{profile?.truck?.registrationNumber || "No truck assigned"}</Text></View><View style={styles.statusPill}><Text style={styles.statusPillText}>{profile?.truck?.status || "offline"}</Text></View></View><View style={styles.rule} /><View style={styles.row}><Metric label="Cargo" value={shipment?.cargoType || "No active shipment"} /><Metric label="Weight" value={shipment ? `${shipment.weightKg.toLocaleString()} kg` : "—"} /><Metric label="State" value={state.replaceAll("_", " ")} /></View></View>
    {/* Cold Chain Live Telemetry */}
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <View>
          <Text style={styles.cardKicker}>COLD CHAIN TELEMETRY</Text>
          <Text style={styles.cardTitle}>4.1°C (Nominal)</Text>
        </View>
        <View style={styles.statusPill}>
          <Text style={styles.statusPillText}>2.0°C – 8.0°C TARGET</Text>
        </View>
      </View>
      <View style={styles.rule} />
      <View style={styles.row}>
        <Metric label="Ambient Temp" value="34.8°C" />
        <Metric label="Compressor" value="Active (92%)" />
        <Metric label="IoT Link" value="Online (LTE)" />
      </View>
    </View>

    <Pressable onPress={sendSos} style={({ pressed }) => [styles.sos, pressed && { transform: [{ scale: .985 }] }]}>
      <View style={styles.sosIcon}><MaterialCommunityIcons name="alarm-light-outline" size={28} color="#FFF" /></View>
      <View style={{ flex: 1 }}>
        <Text style={styles.sosKicker}>EMERGENCY PROTOCOL</Text>
        <Text style={styles.sosTitle}>1-touch breakdown SOS</Text>
        <Text style={styles.sosBody}>Share your GPS position with dispatch and start a rescue match.</Text>
      </View>
      <MaterialCommunityIcons name="chevron-right" size={22} color="#FFF" />
    </Pressable>

    <Text style={styles.sectionTitle}>Rescue status</Text>
    <View style={styles.card}>
      <StatusStep label="Breakdown reported" done={state !== "IN_TRANSIT"} />
      <StatusStep label="Operations matching" done={!["IN_TRANSIT", "BREAKDOWN_REPORTED"].includes(state)} />
      <StatusStep label="Driver handover" done={["CARGO_TRANSFER", "RESCUE_IN_TRANSIT", "DELIVERED", "VERIFICATION", "ESCROW_RELEASED"].includes(state)} />
      <StatusStep label="Pallet Escrow Verified" done={["VERIFICATION", "ESCROW_RELEASED"].includes(state)} />
    </View>

    <Pressable
      onPress={() => {
        Alert.alert(
          "Pallet Handover QR Verification",
          `Shipment ID: ${shipment?.id || "SHP-8492"}\nReefer Integrity: VERIFIED (4.1°C)\nDigital Seal: MATCHED (SHA-256 Validated)\n\nConfirm physical cargo transfer to rescue vehicle?`,
          [
            { text: "Cancel", style: "cancel" },
            {
              text: "Confirm Handover",
              onPress: () => {
                setNotice("Pallet transfer confirmed. Escrow smart contract verified successfully.");
              }
            }
          ]
        );
      }}
      style={styles.verifyButton}
    >
      <MaterialCommunityIcons name="qrcode-scan" size={20} color="#FFF" />
      <Text style={styles.verifyButtonText}>Verify Pallet Handover QR</Text>
    </Pressable>

    <View style={styles.quickRow}>
      <QuickAction icon="warehouse" title="Cold storage" detail="Nearest facilities" />
      <QuickAction icon="phone-outline" title="NHAI helpline" detail="Dial 1033" />
    </View>
  </ScrollView></SafeAreaView>;
}

function Metric({ label, value }: { label: string; value: string }) { return <View style={styles.metric}><Text style={styles.metricLabel}>{label.toUpperCase()}</Text><Text style={styles.metricValue} numberOfLines={1}>{value}</Text></View>; }
function StatusStep({ label, done }: { label: string; done: boolean }) { return <View style={styles.step}><View style={[styles.stepDot, done && { backgroundColor: C.teal }]}>{done && <MaterialCommunityIcons name="check" size={12} color="#FFF" />}</View><Text style={[styles.stepText, done && { color: C.ink }]}>{label}</Text><Text style={styles.stepState}>{done ? "DONE" : "WAITING"}</Text></View>; }
function QuickAction({ icon, title, detail }: { icon: keyof typeof MaterialCommunityIcons.glyphMap; title: string; detail: string }) { return <View style={styles.quick}><MaterialCommunityIcons name={icon} size={20} color={C.teal} /><Text style={styles.quickTitle}>{title}</Text><Text style={styles.quickDetail}>{detail}</Text></View>; }
function Centered({ children }: { children: React.ReactNode }) { return <View style={[styles.safe, { alignItems: "center", justifyContent: "center" }]}>{children}</View>; }

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: C.canvas },
  loginWrap: { flexGrow: 1, justifyContent: "center", padding: 24 },
  brandMark: { width: 48, height: 48, borderRadius: 14, backgroundColor: C.ink, alignItems: "center", justifyContent: "center", marginBottom: 12 },
  brand: { fontSize: 28, fontWeight: "800", color: C.ink, letterSpacing: -1 },
  brandSmall: { fontSize: 18, fontWeight: "800", color: C.ink, letterSpacing: -.5 },
  eyebrow: { fontSize: 10, color: C.teal, fontWeight: "800", letterSpacing: 1.2, marginTop: 3 },
  loginTitle: { fontSize: 27, fontWeight: "800", color: C.ink, marginTop: 32, letterSpacing: -.6 },
  subtitle: { color: C.muted, fontSize: 13, lineHeight: 19, marginTop: 6 },
  formCard: { backgroundColor: C.card, borderRadius: 16, borderWidth: 1, borderColor: C.line, padding: 18, marginTop: 20 },
  label: { color: C.muted, fontSize: 11, fontWeight: "700", marginBottom: 7, marginTop: 4 },
  input: { backgroundColor: "#FAFCFB", borderColor: C.line, borderWidth: 1, borderRadius: 10, height: 46, paddingHorizontal: 14, color: C.ink, marginBottom: 10 },
  primaryButton: { backgroundColor: C.teal, height: 48, borderRadius: 11, alignItems: "center", justifyContent: "center", marginTop: 6 },
  primaryText: { color: "#FFF", fontWeight: "800", fontSize: 14 },
  demoButton: { backgroundColor: "#E4F3EF", height: 42, borderRadius: 11, alignItems: "center", justifyContent: "center", marginTop: 10, borderWidth: 1, borderColor: "#C7E5DC" },
  demoButtonText: { color: C.teal, fontWeight: "800", fontSize: 13 },
  error: { color: C.red, fontSize: 12, lineHeight: 18, marginBottom: 6 },
  footerNote: { color: C.muted, textAlign: "center", fontSize: 11, lineHeight: 17, marginTop: 18, paddingHorizontal: 24 },
  homeWrap: { padding: 20, paddingBottom: 34 },
  topbar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  avatar: { width: 38, height: 38, borderRadius: 19, backgroundColor: C.ink, alignItems: "center", justifyContent: "center" },
  avatarText: { color: "#FFF", fontSize: 12, fontWeight: "800" },
  greeting: { marginTop: 24, flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  title: { color: C.ink, fontWeight: "800", fontSize: 24, letterSpacing: -.5 },
  online: { flexDirection: "row", alignItems: "center", backgroundColor: C.mint, paddingHorizontal: 9, paddingVertical: 6, borderRadius: 14 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: C.teal, marginRight: 5 },
  onlineText: { color: C.teal, fontSize: 9, fontWeight: "800" },
  errorBox: { backgroundColor: C.redSoft, padding: 12, borderRadius: 10, marginTop: 14 },
  notice: { marginTop: 14, backgroundColor: C.mint, padding: 12, borderRadius: 10, flexDirection: "row", alignItems: "center", gap: 8 },
  noticeText: { flex: 1, color: C.teal, fontSize: 12, lineHeight: 17 },
  card: { backgroundColor: C.card, borderRadius: 15, borderWidth: 1, borderColor: C.line, padding: 16, marginTop: 14 },
  cardHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "flex-start" },
  cardKicker: { color: C.muted, fontSize: 9, fontWeight: "800", letterSpacing: 1 },
  cardTitle: { color: C.ink, fontSize: 19, fontWeight: "800", marginTop: 5 },
  statusPill: { backgroundColor: C.mint, borderRadius: 12, paddingHorizontal: 9, paddingVertical: 5 },
  statusPillText: { color: C.teal, fontSize: 9, fontWeight: "800" },
  rule: { height: 1, backgroundColor: C.line, marginVertical: 13 },
  row: { flexDirection: "row", gap: 10 },
  metric: { flex: 1 },
  metricLabel: { color: C.muted, fontSize: 9, fontWeight: "800" },
  metricValue: { color: C.ink, fontSize: 12, fontWeight: "700", marginTop: 5 },
  sos: { backgroundColor: C.red, borderRadius: 15, padding: 16, marginTop: 14, flexDirection: "row", alignItems: "center", gap: 12 },
  sosIcon: { width: 48, height: 48, borderRadius: 13, backgroundColor: "rgba(255,255,255,.16)", alignItems: "center", justifyContent: "center" },
  sosKicker: { color: "rgba(255,255,255,.72)", fontSize: 9, fontWeight: "800", letterSpacing: 1 },
  sosTitle: { color: "#FFF", fontSize: 16, fontWeight: "800", marginTop: 3 },
  sosBody: { color: "rgba(255,255,255,.82)", fontSize: 11, lineHeight: 16, marginTop: 4 },
  verifyButton: { backgroundColor: C.ink, height: 46, borderRadius: 12, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, marginTop: 14 },
  verifyButtonText: { color: "#FFF", fontWeight: "800", fontSize: 13 },
  sectionTitle: { color: C.ink, fontSize: 15, fontWeight: "800", marginTop: 20, marginBottom: -2 },
  step: { flexDirection: "row", alignItems: "center", paddingVertical: 7 },
  stepDot: { width: 22, height: 22, borderRadius: 11, backgroundColor: "#DCE5E1", alignItems: "center", justifyContent: "center", marginRight: 10 },
  stepText: { color: C.muted, fontSize: 12, flex: 1, fontWeight: "600" },
  stepState: { color: C.muted, fontSize: 9, fontWeight: "800" },
  quickRow: { flexDirection: "row", gap: 10, marginTop: 14 },
  quick: { backgroundColor: C.card, borderColor: C.line, borderWidth: 1, borderRadius: 13, padding: 13, flex: 1 },
  quickTitle: { color: C.ink, fontWeight: "800", fontSize: 12, marginTop: 10 },
  quickDetail: { color: C.muted, fontSize: 10, marginTop: 3 }
});
