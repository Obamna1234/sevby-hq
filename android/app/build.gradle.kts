plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing comes from environment variables (set by the GitHub workflow from repo secrets).
// If they are missing (e.g. a local build in Android Studio), release builds use the debug key.
val ksFile = System.getenv("SEVBYHQ_KEYSTORE")
val hasReleaseKey = !ksFile.isNullOrBlank() && file(ksFile).exists()

/**
 * Android's internal version number, worked out from the version name so it always goes up, whichever
 * workflow built the APK: 1.0.4 → 10_004_999, 1.1.0-beta.2 → 10_100_402, 1.0.5-dev.37 → 10_005_037.
 * A finished version beats its rc / beta / alpha / dev builds; dev builds of the next version install
 * over the current release, and the release installs over them.
 */
fun versionCodeOf(name: String): Int {
    val core = name.substringBefore('-').split('.').map { it.toIntOrNull() ?: 0 }
    val pre = name.substringAfter('-', "").lowercase().split('.', '-').filter { it.isNotEmpty() }
    val n = pre.lastOrNull()?.toIntOrNull() ?: 0
    val suffix = when (pre.firstOrNull()) {
        null -> 999
        "rc" -> 800 + minOf(n, 199)
        "beta" -> 400 + minOf(n, 399)
        "alpha" -> 200 + minOf(n, 199)
        else -> minOf(n, 199)                       // dev
    }
    return core.getOrElse(0) { 0 } * 10_000_000 + core.getOrElse(1) { 0 } * 100_000 +
        core.getOrElse(2) { 0 } * 1_000 + suffix
}
val appVersionName = System.getenv("SEVBYHQ_VERSION_NAME") ?: "0.10.3-dev.0"

android {
    namespace = "io.github.obamna1234.sevby"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.obamna1234.sevbyhq"
        minSdk = 24
        targetSdk = 34
        versionCode = versionCodeOf(appVersionName)
        versionName = appVersionName

        ndk {
            abiFilters += listOf("armeabi-v7a", "arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(ksFile!!)
                storePassword = System.getenv("SEVBYHQ_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("SEVBYHQ_KEY_ALIAS")
                keyPassword = System.getenv("SEVBYHQ_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (hasReleaseKey) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    // One small APK per phone type, plus a universal one that runs everywhere.
    splits {
        abi {
            isEnable = true
            reset()
            include("armeabi-v7a", "arm64-v8a", "x86_64")
            isUniversalApk = true
        }
    }

    // The bundled Python / FFmpeg are run as programs, so they must be unpacked on install.
    packaging {
        jniLibs { useLegacyPackaging = true }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures { viewBinding = true }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

val youtubedlAndroid = "0.18.1"

dependencies {
    implementation("io.github.junkfood02.youtubedl-android:library:$youtubedlAndroid")
    implementation("io.github.junkfood02.youtubedl-android:ffmpeg:$youtubedlAndroid")

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.documentfile:documentfile:1.0.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    testImplementation("junit:junit:4.13.2")
}
