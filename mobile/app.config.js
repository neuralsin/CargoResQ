module.exports = ({ config }) => ({
  ...config,
  name: "CargoResQ Driver",
  slug: "cargoresq-driver",
  version: "1.0.0",
  orientation: "portrait",
  userInterfaceStyle: "light",
  scheme: "cargoresq-driver",
  plugins: ["expo-location", "expo-secure-store"],
  android: {
    package: "com.cargoresq.driver",
    versionCode: 1,
    permissions: ["ACCESS_COARSE_LOCATION", "ACCESS_FINE_LOCATION"],
    adaptiveIcon: {
      backgroundColor: "#101615"
    }
  },
  extra: {
    ...(config.extra || {}),
    apiBaseUrl: process.env.EXPO_PUBLIC_API_BASE_URL || "https://api.cargoresq.com"
  }
});
