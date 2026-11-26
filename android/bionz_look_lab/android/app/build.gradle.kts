plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.bionz.looklab"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = "28.2.13676358"

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "com.bionz.looklab"
        minSdk = 29
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

// GNU Make in ndk-build rejects spaces in absolute project paths ("a6400 project"),
// so run ndk-build from src/main/cpp with relative paths into src/main/jniLibs.
val buildNativeRawLib by tasks.registering(Exec::class) {
    workingDir = file("src/main/cpp")
    inputs.files(
        file("src/main/cpp/Android.mk"),
        file("src/main/cpp/Application.mk"),
        file("src/main/cpp/arw_decoder.cpp")
    )
    outputs.file(file("src/main/jniLibs/arm64-v8a/libbionz_raw.so"))
    val ndkDir = "${android.sdkDirectory}/ndk/28.2.13676358"
    commandLine(
        "$ndkDir/ndk-build",
        "NDK_PROJECT_PATH=.",
        "APP_BUILD_SCRIPT=Android.mk",
        "NDK_APPLICATION_MK=Application.mk",
        "NDK_OUT=/tmp/bionz_looklab_ndk_out",
        "NDK_LIBS_OUT=../jniLibs",
        "-j4"
    )
}

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("JniLibFolders") }.configureEach {
    dependsOn(buildNativeRawLib)
}
