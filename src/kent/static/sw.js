self.addEventListener("install", e => {
    console.log("[ServiceWorker] - Install");
});

self.addEventListener("activate", e => {
    console.log("[ServiceWorker] - Activate");
    // e.waitUntil((async () => {
    //     // Get a list of all your caches in your app
    //     const keyList = await caches.keys();
    //     await Promise.all(
    //         keyList.map(key => {
    //             console.log(key);
    //             /* 
    //                Compare the name of your current cache you are iterating through
    //                and your new cache name
    //             */
    //             if (key !== cacheName) {
    //                 console.log("[ServiceWorker] - Removing old cache", key);
    //                 return caches.delete(key);
    //             }
    //         })
    //     );
    // })());
    // e.waitUntil(self.clients.claim());
});

self.addEventListener("change", e => {
    console.log("[ServiceWorker] - change");
})
self.addEventListener("*", e => {
    console.log("[ServiceWorker] - *");
})

self.addEventListener("message", e => {
    console.log("message", e.data)
})

self.onnotificationclick = (event) => {
    console.log("Notification clicked")
};
