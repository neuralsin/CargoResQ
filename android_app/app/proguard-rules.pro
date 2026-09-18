# Model classes are deserialised by name from JSON responses.
-keep class com.cargoresq.driver.model.** { *; }

# osmdroid reflects over its configuration and tile providers.
-keep class org.osmdroid.** { *; }
-dontwarn org.osmdroid.**

# OkHttp ships optional platform integrations that are absent on Android.
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
