plugins {
    id("com.android.application")
    kotlin("android")
}

android {
    namespace = "com.memeocr.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.memeocr.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 2
        versionName = "1.1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation(project(":core"))
    implementation("androidx.exifinterface:exifinterface:1.3.7")
    implementation("com.google.mlkit:text-recognition-chinese:16.0.1")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
