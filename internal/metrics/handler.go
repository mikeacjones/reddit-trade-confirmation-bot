// Package metrics provides a minimal Prometheus text exposition for the
// Temporal SDK MetricsHandler, using only the Go standard library.
package metrics

import (
	"fmt"
	"math"
	"net/http"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"go.temporal.io/sdk/client"
)

// Handler implements client.MetricsHandler and serves /metrics.
type Handler struct {
	tags map[string]string
	reg  *registry
}

type registry struct {
	mu       sync.Mutex
	counters map[string]*atomic.Int64
	gauges   map[string]*atomic.Uint64 // float bits
	server   *http.Server
}

// New starts an HTTP server on bindAddr (e.g. "0.0.0.0:9000") exposing /metrics.
func New(bindAddr string, globalTags map[string]string) (*Handler, error) {
	reg := &registry{
		counters: map[string]*atomic.Int64{},
		gauges:   map[string]*atomic.Uint64{},
	}
	h := &Handler{tags: copyTags(globalTags), reg: reg}
	mux := http.NewServeMux()
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
		_, _ = w.Write([]byte(reg.expose()))
	})
	reg.server = &http.Server{Addr: bindAddr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() { _ = reg.server.ListenAndServe() }()
	return h, nil
}

// Close shuts down the metrics HTTP server.
func (h *Handler) Close() error {
	if h.reg.server == nil {
		return nil
	}
	return h.reg.server.Close()
}

func (h *Handler) WithTags(tags map[string]string) client.MetricsHandler {
	merged := copyTags(h.tags)
	for k, v := range tags {
		merged[k] = v
	}
	return &Handler{tags: merged, reg: h.reg}
}

func (h *Handler) Counter(name string) client.MetricsCounter {
	key := seriesKey(sanitize(name)+"_total", h.tags)
	c := h.reg.counter(key)
	return counterFunc(func(d int64) { c.Add(d) })
}

func (h *Handler) Gauge(name string) client.MetricsGauge {
	key := seriesKey(sanitize(name), h.tags)
	g := h.reg.gauge(key)
	return gaugeFunc(func(v float64) {
		g.Store(floatBits(v))
	})
}

func (h *Handler) Timer(name string) client.MetricsTimer {
	// Record timers as a simple counter of observations + sum gauge for latency.
	countKey := seriesKey(sanitize(name)+"_count", h.tags)
	sumKey := seriesKey(sanitize(name)+"_sum_seconds", h.tags)
	c := h.reg.counter(countKey)
	s := h.reg.gauge(sumKey)
	return timerFunc(func(d time.Duration) {
		c.Add(1)
		for {
			old := s.Load()
			next := floatFromBits(old) + d.Seconds()
			if s.CompareAndSwap(old, floatBits(next)) {
				return
			}
		}
	})
}

func (r *registry) counter(key string) *atomic.Int64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	if c, ok := r.counters[key]; ok {
		return c
	}
	c := &atomic.Int64{}
	r.counters[key] = c
	return c
}

func (r *registry) gauge(key string) *atomic.Uint64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	if g, ok := r.gauges[key]; ok {
		return g
	}
	g := &atomic.Uint64{}
	r.gauges[key] = g
	return g
}

func (r *registry) expose() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	var b strings.Builder
	keys := make([]string, 0, len(r.counters)+len(r.gauges))
	for k := range r.counters {
		keys = append(keys, "c:"+k)
	}
	for k := range r.gauges {
		keys = append(keys, "g:"+k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		kind, key := k[:1], k[2:]
		name, labels := splitKey(key)
		if kind == "c" {
			fmt.Fprintf(&b, "%s%s %d\n", name, labels, r.counters[key].Load())
		} else {
			fmt.Fprintf(&b, "%s%s %g\n", name, labels, floatFromBits(r.gauges[key].Load()))
		}
	}
	return b.String()
}

type counterFunc func(int64)

func (f counterFunc) Inc(d int64) { f(d) }

type gaugeFunc func(float64)

func (f gaugeFunc) Update(v float64) { f(v) }

type timerFunc func(time.Duration)

func (f timerFunc) Record(d time.Duration) { f(d) }

func copyTags(in map[string]string) map[string]string {
	out := make(map[string]string, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

func sanitize(name string) string {
	name = strings.ReplaceAll(name, "-", "_")
	name = strings.ReplaceAll(name, "/", "_")
	name = strings.ReplaceAll(name, ".", "_")
	return name
}

func seriesKey(name string, tags map[string]string) string {
	if len(tags) == 0 {
		return name + "|"
	}
	keys := make([]string, 0, len(tags))
	for k := range tags {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	b.WriteString(name)
	b.WriteByte('|')
	b.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, `%s="%s"`, sanitize(k), escapeLabel(tags[k]))
	}
	b.WriteByte('}')
	return b.String()
}

func splitKey(key string) (name, labels string) {
	i := strings.IndexByte(key, '|')
	if i < 0 {
		return key, ""
	}
	return key[:i], key[i+1:]
}

func escapeLabel(v string) string {
	v = strings.ReplaceAll(v, `\`, `\\`)
	v = strings.ReplaceAll(v, `"`, `\"`)
	v = strings.ReplaceAll(v, "\n", `\n`)
	return v
}

func floatBits(f float64) uint64 { return math.Float64bits(f) }

func floatFromBits(u uint64) float64 { return math.Float64frombits(u) }
