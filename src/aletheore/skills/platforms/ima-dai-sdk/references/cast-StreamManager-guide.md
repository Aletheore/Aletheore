# Google IMA DAI SDK Cast - StreamManager Guide

Use `StreamManager` to request and play a livestream or VOD stream from Google
full-service DAI in a Cast Application Framework (CAF) Web Receiver.

## Table of Contents

*   [Import the IMA DAI SDK](#import-the-ima-dai-sdk) (Line 17)
*   [SDK Initialization](#sdk-initialization) (Line 28)
*   [Make stream requests](#make-stream-requests) (Line 43)
*   [Stream Event and Error Handling](#stream-event-and-error-handling)
    (Line 95)
    *   [DAI session events](#dai-session-events) (Line 103)
    *   [Ad break events](#ad-break-events) (Line 108)
    *   [Ad events](#ad-events) (Line 114)
    *   [Cleanup](#cleanup) (Line 124)

## Import the IMA DAI SDK

Add the script tag for the IMA DAI SDK for CAF immediately after the CAF
receiver framework script loader. The CAF DAI SDK is evergreen, so there is no
need to set a specific version.

```html
<script src="//www.gstatic.com/cast/sdk/libs/caf_receiver/v3/cast_receiver_framework.js"></script>
<script src="//imasdk.googleapis.com/js/sdkloader/cast_dai.js"></script>
```

## SDK Initialization

Obtain the `CastReceiverContext` and `PlayerManager` instances.

Initialize the `StreamManager` so that the IMA DAI SDK can set up timed metadata
event listeners before playback starts.

```typescript
const castContext: cast.framework.CastReceiverContext =
    cast.framework.CastReceiverContext.getInstance();
const playerManager: cast.framework.PlayerManager =
    castContext.getPlayerManager();
const streamManager = new google.ima.cast.dai.api.StreamManager();
```

## Make stream requests

Create `google.ima.cast.dai.api.LiveStreamRequest` from sender app's cast
request.

```typescript
let streamRequest = new google.ima.cast.dai.api.LiveStreamRequest();
streamRequest.networkCode = <NETWORK_CODE_PLACEHOLDER>;
streamRequest.assetKey = <ASSET_KEY_PLACEHOLDER>;
```

Extract the following parameters from
`cast.framework.messages.MediaInformation.customData`:

*   **<NETWORK_CODE_PLACEHOLDER>**: The Google Ad Manager network code.
*   **<ASSET_KEY_PLACEHOLDER>**: The livestream asset key configured in Google
    Ad Manager.

For VOD, create `google.ima.cast.dai.api.VODStreamRequest` from sender app's
cast request.

```typescript
let streamRequest = new google.ima.cast.dai.api.VODStreamRequest();
streamRequest.networkCode = <NETWORK_CODE_PLACEHOLDER>;
streamRequest.contentSourceId = <CMS_ID_PLACEHOLDER>;
streamRequest.videoId = <VIDEO_ID_PLACEHOLDER>;
```

Provide the following parameters from
`cast.framework.messages.MediaInformation.customData`:

*   **<NETWORK_CODE_PLACEHOLDER>**: The Google Ad Manager network code.
*   **<CMS_ID_PLACEHOLDER>**: The content source ID (CMS ID) in Google Ad
    Manager.
*   **<VIDEO_ID_PLACEHOLDER>**: The video ID in the CMS.

For testing purposes, use values of DAI sample streams from
https://developers.google.com/ad-manager/dynamic-ad-insertion/streams.md.txt?utm_source=agent-skills&utm_medium=content&utm_campaign=adr-ss-ai&utm_content=ima-dai-sdk

To play the stream immediately, create a load message interceptor to make stream
requests by calling `streamManager.requestStream()`:

```typescript
playerManager.setMessageInterceptor(
  cast.framework.messages.MessageType.LOAD,
  (loadRequestData: cast.framework.messages.LoadRequestData) => {
    return streamManager.requestStream(loadRequestData, streamRequest);
    }
  );
```

## Stream Event and Error Handling

Add listeners on `google.ima.cast.dai.api.StreamManager` for all event types:

*   DAI session events and errors
*   Ad break events
*   Individual ad events

### DAI session events

Listen for the `google.ima.cast.dai.api.StreamEvent.Type.ERROR` event to log the
`event.getStreamData().errorMessage` string and switch to a fallback stream.

### Ad break events

Listen for the `google.ima.cast.dai.api.StreamEvent.Type.AD_PERIOD_STARTED` and
`google.ima.cast.dai.api.StreamEvent.Type.AD_PERIOD_ENDED` events to disable and
restore playback controls, such as seeking.

### Ad events

Listen for the following events of individual ads to log them:

*   `google.ima.cast.dai.api.StreamEvent.Type.STARTED`
*   `google.ima.cast.dai.api.StreamEvent.Type.FIRST_QUARTILE`
*   `google.ima.cast.dai.api.StreamEvent.Type.MIDPOINT`
*   `google.ima.cast.dai.api.StreamEvent.Type.THIRD_QUARTILE`
*   `google.ima.cast.dai.api.StreamEvent.Type.COMPLETE`

### Cleanup

Reset the `StreamManager` when a stream is ended, or a fatal error occurs.

```typescript
streamManager.reset();
```
