"use strict";

window.messages = window.messages || {};

let nextRequestId = 0;
const outstandingRequests = {};

window.anvilCallIdeFn = (fn, args, timeout = 500) => {
    const msg = {
        type: "CALL",
        id: nextRequestId++,
        fn,
        args,
    };
    const p = new Promise((resolve, reject) => {
        outstandingRequests[msg.id] = { resolve, reject };
    });

    if (window.parent !== window) {
        window.parent.postMessage(msg, "*");
    } else if (window.opener) {
        window.opener.postMessage(msg, "*");
    } else {
        throw new Error("No IDE to talk to.");
    }
    if (timeout) {
        // null or 0 means no timeout
        setTimeout(() => {
            outstandingRequests[msg.id]?.reject(new Error("Timeout"));
        }, timeout);
    }
    return p;
};

$(function () {
    window.addEventListener("message", async function (e) {
        // Filter out messages without data.
        if (!e.data) {
            return;
        }
        //console.log("Runtime client got message: ", e);

        // Check source origin of incoming message. Make sure it's the Anvil IDE.
        if (e.origin != window.anvilParams.ideOrigin && !window.anvilAppOrigin.startsWith(e.origin)) {
            //console.warn("Ignoring message from invalid origin:", e.origin);
            return;
        }

        if (e.data.response || e.data.error) {
            const { id, response, error } = e.data;
            const request = outstandingRequests[id];
            if (request) {
                delete outstandingRequests[id];
                if (response) {
                    request.resolve(response);
                } else {
                    request.reject(error);
                }
            }
        } else {
            var fn = window.messages[e.data.fn];
            var rv;
            try {
                if (fn) {
                    rv = { result: await fn.call(window.messages, e.data.args) };
                } else {
                    console.debug("Message not recognised:", e.data);
                    //rv = {error: "Message '"+e.data.fn+"' not recognised"};
                }
            } catch (err) {
                console.error(err, err.stack || "(no stack trace)");
                if (err instanceof Sk.builtin.BaseException) {
                    rv = {
                        fn: "pythonError",
                        traceback: err.traceback,
                        type: err.tp$name,
                        msg: Sk.ffi.toJs(err.args)[0],
                    };
                } else {
                    rv = { error: "" + err };
                }
            }

            if (rv) {
                rv.requestId = e.data.requestId;

                if (window.parent !== window) {
                    window.parent.postMessage(rv, e.origin);
                } else if (window.opener) {
                    window.opener.postMessage(rv, e.origin);
                }
            }
        }
    });
    window.parent.postMessage({ fn: "ready" }, "*");
    window.opener?.postMessage({ fn: "ready" }, "*");
});
