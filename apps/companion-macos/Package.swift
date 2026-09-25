// swift-tools-version: 5.10

import PackageDescription

let package = Package(
    name: "SixSentencesCompanion",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "SixSentencesCompanion", targets: ["SixSentencesCompanion"]),
    ],
    dependencies: [
        .package(
            url: "https://github.com/sparkle-project/Sparkle",
            exact: "2.10.0"
        ),
    ],
    targets: [
        .executableTarget(
            name: "SixSentencesCompanion",
            dependencies: [
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "Sources/SixSentencesCompanion"
        ),
        .testTarget(
            name: "SixSentencesCompanionTests",
            dependencies: ["SixSentencesCompanion"],
            path: "Tests/SixSentencesCompanionTests"
        ),
    ]
)
