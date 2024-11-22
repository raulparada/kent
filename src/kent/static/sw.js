/*
    Kent notifications ServiceWorker.
*/

self.addEventListener("install", event => {
    console.log("[kent notifications sw] Installed");
});

self.addEventListener("activate", event => {
    console.log("[kent notifications sw] Activated");
});

self.onnotificationclick = (event) => {
    console.log("Notification clicked", event)
    event.notification.close();

    const eventUrl = event.notification.data.url;
    let targetUrl;
    switch (event.action) {
        case "relay":
            targetUrl = eventUrl + "/relay";
            break;
        default:
            targetUrl = eventUrl
    }
    event.waitUntil(
        clients.matchAll({ type: "window" }).then((clientsArr) => {
            // If a Window tab matching the targeted URL already exists, focus that;
            const hadWindowToFocus = clientsArr.some((windowClient) =>
                windowClient.url === targetUrl
                    ? (windowClient.focus(), true)
                    : false,
            );
            // Otherwise, open a new tab to the applicable URL and focus it.
            if (!hadWindowToFocus)
                clients
                    .openWindow(targetUrl)
                    .then((windowClient) => (windowClient ? windowClient.focus() : null));
        }),
    );
};
