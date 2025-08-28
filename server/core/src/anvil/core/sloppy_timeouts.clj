(ns anvil.core.sloppy-timeouts
  (:require [anvil.util :as util]
            [clojure.tools.logging :as log])
  (:import (java.util Timer TimerTask)))

;; For dealing with Media, we require timeouts that are very fast to set/reset with high parallelism

;(clj-logging-config.log4j/set-logger! :level :trace)

(defprotocol SloppyTimeout
  (set-timeout [this key timeout-sec])
  (clear-timeout [this key]))

(defonce timer (Timer. true))

(defn mk-SloppyTimeout [slop-sec on-timeout]
  (let [timeouts (atom {})
        slop-ms (long (* slop-sec 1000))]
    (reify SloppyTimeout
      (set-timeout [this key timeout-sec]
        (let [timeout-ms (long (* timeout-sec 1000))
              [old-t new-t] (swap-vals! timeouts update key
                                        (fn [{:keys [expires timer-task] :as original-value}]
                                          (let [now (System/currentTimeMillis)]
                                            (if (or (nil? original-value) (< expires (+ now timeout-ms)))
                                              {:expires    (+ now timeout-ms slop-ms)
                                               :timer-task (util/timer-task ::expiry
                                                             (swap! timeouts dissoc key)
                                                             (on-timeout key))}
                                              original-value))))
              old-v (get old-t key)
              new-v (get new-t key)]
          (when (or (not= (:timer-task old-v) (:timer-task new-v)))
            (when-let [^TimerTask tt (:timer-task old-v)]
              (log/trace "Cancelling old task for:" key)
              (.cancel tt))
            (log/trace "Scheduling for:" key)
            (.schedule timer ^TimerTask (:timer-task new-v) ^long (+ timeout-ms slop-ms)))))
      (clear-timeout [this key]
        (let [[old-t new-t] (swap-vals! timeouts dissoc key)]
          (when-let [old-task (get-in old-t [key :timer-task])]
            (log/trace "Cancelling:" key)
            (.cancel old-task)))))))
