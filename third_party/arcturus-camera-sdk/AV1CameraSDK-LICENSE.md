# Arcturus Vision Camera Preview SDK Developer License Agreement

Version: Nov 8, 2026

# 1. Parties, acceptance and scope

This agreement is between Arcturus Industries LLC (Arcturus Vision) ("Arcturus") and the accepting individual or organization ("you"). You must have authority to bind any organization you represent. Developer access requires explicit acceptance through Arcturus's process.

**Accurate identity is required for authorized access.** Access or downloads obtained through impersonation or materially false identity, organization or authority information confer no license. You must stop use and delete all copies obtained this way. Arcturus may revoke access.

**"Preview SDK"** means all materials downloaded from the Arcturus Vision Camera Preview SDK website or otherwise provided by Arcturus concurrently with the Preview SDK, including headers, libraries, source code, tools, examples, documentation, and their copies and modifications. Within the Preview SDK, **"Client Library"** means the Arcturus-provided camera client static library (`libAV1CameraClient.a`), and any copies, portions or modifications of that binary. The public headers, including `AV1CameraClient.h` and `AV1CameraIPCProtocol.h`, declare its interface and are not part of the Client Library. **"Camera Interface"** means the camera IPC interface accessed by the Client Library.

**"Applications"** means end-user applications you develop or adapt using the Preview SDK or knowledge of the Camera Interface obtained from it, including independently implemented IPC clients. Development tools and SDKs that merely enable others to build Applications are not themselves Applications; any end-user camera features they provide are subject to section 3.

**"Camera Data"** means camera images, video, frames and information derived from them, including extracted text, detections, scene descriptions, spatial maps, features and embeddings. Compression, transformation or de-identification does not exempt it from these conditions.

Sample code identified as MIT-licensed and other separately licensed materials are governed by their respective licenses; this agreement does not limit rights granted under those licenses.

# 2. Development and application distribution

Subject to this agreement, Arcturus grants you a non-exclusive license to copy, inspect, modify, compile and use the Preview SDK for development, testing, demonstration and private use of Applications and related development tools or SDKs, including personal use and internal organizational use. You may work with external developers and testers, subject to the access, distribution and user-consent requirements below.

You may directly share compiled Application binaries with the Client Library statically linked into them with users or developers for testing or private use, subject to the distribution restrictions below. Preserve this agreement and applicable copyright notices with those binaries. You may share or publish your own source code for Applications, tools or SDKs based on or using the Client Library, provided you do not bundle the Client Library itself. You may redistribute the public headers, including their contents and modifications, with your code or separately, while preserving this agreement and applicable copyright notices. Developers needing the Client Library must obtain it through Arcturus's acceptance process. End-user Applications must comply with section 3.

You must require downstream developers to accept this agreement through Arcturus's process before using your tools or SDKs to access the Camera Interface. You may distribute your own tools or SDKs in source or compiled binary form without incorporating the Client Library. Compiled tools or SDKs may leave references to Client Library symbols unresolved for the Application developer to resolve by linking their own authorized copy of `libAV1CameraClient.a` into the final Application. Tools and SDKs must not redistribute the Client Library, whether as an archive, extracted object files, incorporated into another binary, renamed or modified. Rights granted under separate licenses remain unaffected.

**You must not publish, distribute or make Applications or the Preview SDK available through any marketplace, including Steam, whether free or paid and including marketplace testing programs.** This does not prohibit sharing your own source code, distributing development tools or SDKs without the Client Library, or redistributing public headers as expressly permitted above; neither permission authorizes marketplace distribution of end-user Applications or the Client Library. Additional distribution rights require Arcturus's express written authorization.

# 3. Camera awareness and user control

This section applies to end-user camera features in Applications, including during testing and demonstrations, and to services processing Camera Data for them.

Applications must not use private or internal APIs to access camera functionality, including to bypass user consent.

## 3.1 Informed camera use

Make camera activity and the use of Camera Data, including derived information, obvious to users. Camera use requires an informed user action; opening an Application with a clear camera purpose may suffice without an extra prompt or visible feed.

## 3.2 Recording, storage, and transmission

Before recording, persistently saving or sending Camera Data to servers or other recipients, obtain explicit, informed authorization clearly identifying the data, recipients and purposes, including model training or onward sharing. Camera access alone does not authorize these activities. Make recording and transmission apparent. Enabling LEDs to indicate recording is strongly recommended and must be enabled where required by applicable local laws. Stop image processing and recording on headset removal unless the user is aware of continuation. Using LEDs can help make this clear.

# 4. Ownership and feedback

Arcturus retains its Preview SDK rights. You retain your application code, subject to this agreement's use and distribution conditions.

Feedback is optional. Submitting it grants Arcturus perpetual, non-exclusive, worldwide, royalty-free permission to use, reproduce and adapt it to develop, improve, document and distribute products. This neither transfers ownership nor covers application code or other materials not submitted as feedback.

# 5. Experimental software

The Preview SDK is experimental. Arcturus may change or discontinue it without promising production release, support or suitability for any use. To the extent permitted by law, it is supplied without warranties or liability for evaluation or use losses; legally non-excludable liability remains unaffected.

# 6. Termination

Your license ends upon breach or Arcturus's written termination notice. Stop using the Preview SDK and Camera Interface under this agreement and delete SDK copies and modifications, except legally required archival copies, which must not be used for development or operation.

Ownership, feedback permissions, distribution restrictions, user-data obligations, disclaimers, indemnification and rights reserved by law survive.

# 7. Rights reserved by law

Rights that applicable law prohibits restricting remain unaffected.

# 8. Severability and governing law

**Severability.** If any provision of this agreement is held invalid or unenforceable, the remaining provisions remain in effect to the extent permitted by law.

**Governing law.** This agreement is governed by the laws of the State of Washington, excluding its conflict-of-laws rules.

# 9. Indemnification

To the extent permitted by law, you will defend, indemnify and hold harmless Arcturus and its officers, employees and agents from third-party claims and resulting damages, liabilities, costs and reasonable legal fees arising from your Applications, tools or SDKs, your handling of Camera Data, or your breach of this agreement, except to the extent caused by Arcturus's breach of this agreement, negligence or willful misconduct.

Arcturus will promptly notify you of a claim, provide reasonable cooperation at your expense and allow you to control the defense with qualified counsel. Delayed notice relieves your obligations only to the extent it materially prejudices the defense. You may not settle a claim without fully releasing the indemnified parties, or impose an admission of fault or nonmonetary obligation on them without Arcturus's prior written consent, which will not be unreasonably withheld.
