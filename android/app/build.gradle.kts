plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing comes from environment variables (set by the GitHub workflow from repo secrets).
// If they are missing (e.g. a local build in Android Studio), release builds use the debug key.
val ksFile = System.getenv("SEVBY_KEYSTORE")
val hasReleaseKey = !ksFile.isNullOrBlank() && file(ksFile).exists()

android {
    namespace = "io.github.obamna1234.sevby"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.obamna1234.sevbyhq"
        minSdk = 24
        targetSdk = 34
        versionCode = (System.getenv("SEVBY_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("SEVBY_VERSION_NAME") ?: "0.10.1-beta"

        ndk {
            abiFilters += listOf("armeabi-v7a", "arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(ksFile!!)
                storePassword = System.getenv("SEVBY_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("SEVBY_KEY_ALIAS")
                keyPassword = System.getenv("SEVBY_KEY_PASSWORD")
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
