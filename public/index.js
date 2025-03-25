// console.log(window.location.host)
// let url = `ws://localhost:8000/ws/socket_server/`;

// const chatSocket = new WebSocket(url)

// chatSocket.onmessage = function (e) {
// 	let data = JSON.parse(e.data)
// 	console.log('Data', data)
// }

// const sio = io("ws://0.0.0.0:9000");
// console.log("Hello");
// let stream;
// let videoElement = document.createElement("video");
// videoElement.autoplay = true;
// videoElement.muted = true;
// document.body.appendChild(videoElement); // Add

// sio.on('connect', () => {
// 	startVideoStream();

// });

// sio.on("connection_established", (data) => {
// 	console.log("Server says:", data.message);
// });

// sio.on('disconnect', () => {
// 	console.log("disconnected")
// });

// sio.on("video_chunk", (data) => {
// 	console.log("data", data);
// });

// async function startVideoStream() {
// 	try {
// 		stream = await navigator.mediaDevices.getUserMedia({
// 			video: true,
// 			audio: true,
// 		});

// 		videoElement.srcObject = stream;

// 		// Start sending video chunks every 30 seconds
// 		setInterval(sendVideoChunk, 10000);
// 	} catch (error) {
// 		console.error("❌ Error accessing webcam:", error);
// 	}
// }

// function sendVideoChunk() {
// 	if (!stream) {
// 		console.warn("⚠️ No active video stream.");
// 		return;
// 	}

// 	const canvas = document.createElement("canvas");
// 	const ctx = canvas.getContext("2d");
// 	const videoTrack = stream.getVideoTracks()[0];
// 	const settings = videoTrack.getSettings();

// 	// Set canvas size to match the video feed
// 	canvas.width = settings.width || 640;
// 	canvas.height = settings.height || 480;

// 	// Draw the current frame from the video stream onto the canvas
// 	ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);

// 	// Convert the frame to a Blob (JPEG format) and send via WebSocket
// 	canvas.toBlob((blob) => {
// 		if (blob) {
// 			const reader = new FileReader();
// 			reader.readAsArrayBuffer(blob);
// 			reader.onloadend = () => {
// 				sio.emit("video_chunk", { frame: reader.result });
// 				console.log(reader.result)
// 				console.log("📤 Sent 30-second chunk.");
// 			};
// 		}
// 	}, "image/jpeg");
// }

