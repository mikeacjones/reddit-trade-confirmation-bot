package templates

import (
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

var (
	placeholderRe = regexp.MustCompile(`\{([^{}]+)\}`)
	cacheMu       sync.Mutex
	cache         = map[string]string{}
)

// Loader loads templates from a wiki (optional) or local files.
type Loader struct {
	LoadWiki func(name string) (string, error)
	Dir      string
}

func findTemplatesDir() string {
	candidates := []string{}
	if v := os.Getenv("MDTEMPLATES_DIR"); v != "" {
		candidates = append(candidates, v)
	}
	candidates = append(candidates, "mdtemplates", "/app/mdtemplates")
	if exe, err := os.Executable(); err == nil {
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), "mdtemplates"))
	}
	_, file, _, ok := runtime.Caller(0)
	if ok {
		// internal/templates -> repo root mdtemplates (dev builds)
		candidates = append(candidates, filepath.Join(filepath.Dir(file), "..", "..", "mdtemplates"))
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && st.IsDir() {
			return c
		}
	}
	return "mdtemplates"
}

// New returns a loader rooted at the mdtemplates directory.
func New(loadWiki func(name string) (string, error)) *Loader {
	return &Loader{LoadWiki: loadWiki, Dir: findTemplatesDir()}
}

// Load loads a template from wiki or local file, caching the result.
func (l *Loader) Load(name string) (string, error) {
	cacheMu.Lock()
	defer cacheMu.Unlock()
	if c, ok := cache[name]; ok {
		return c, nil
	}

	var content string
	if l.LoadWiki != nil {
		wiki, err := l.LoadWiki(name)
		if err == nil && wiki != "" {
			content = wiki
			slog.Info("Loaded template from wiki", "template", name)
		}
	}
	if content == "" {
		local, err := l.loadLocal(name)
		if err != nil {
			return "", err
		}
		content = local
		slog.Info("Loaded template from file", "template", name, "path", filepath.Join(l.Dir, name+".md"))
	}
	cache[name] = content
	return content, nil
}

func (l *Loader) loadLocal(name string) (string, error) {
	dir := l.Dir
	if dir == "" {
		dir = findTemplatesDir()
	}
	path := filepath.Join(dir, name+".md")
	b, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// Format loads and formats a template, falling back to local file on format failure.
func (l *Loader) Format(name string, args map[string]any) (string, error) {
	tmpl, err := l.Load(name)
	if err != nil {
		return "", err
	}
	out, err := FormatString(tmpl, args)
	if err == nil {
		return out, nil
	}
	slog.Warn("Template formatting failed; falling back to local file",
		"template", name, "error", err)
	local, lerr := l.loadLocal(name)
	if lerr != nil {
		return "", err
	}
	cacheMu.Lock()
	cache[name] = local
	cacheMu.Unlock()
	return FormatString(local, args)
}

// FormatString replaces {key} and {obj.attr} placeholders like Python str.format.
func FormatString(tmpl string, args map[string]any) (string, error) {
	var firstErr error
	out := placeholderRe.ReplaceAllStringFunc(tmpl, func(m string) string {
		key := m[1 : len(m)-1]
		val, err := resolve(args, key)
		if err != nil {
			if firstErr == nil {
				firstErr = err
			}
			return m
		}
		return fmt.Sprint(val)
	})
	if firstErr != nil {
		return "", firstErr
	}
	return out, nil
}

func resolve(args map[string]any, key string) (any, error) {
	parts := strings.Split(key, ".")
	var cur any = args
	for _, part := range parts {
		c, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("cannot resolve %q", key)
		}
		v, ok := c[part]
		if !ok {
			return nil, fmt.Errorf("missing key %q", key)
		}
		cur = v
	}
	return cur, nil
}

// FormatTitle applies strftime-like formatting for monthly post titles.
func FormatTitle(tmpl string, t time.Time) string {
	replacer := strings.NewReplacer(
		"%B", t.Format("January"),
		"%Y", t.Format("2006"),
		"%m", t.Format("01"),
		"%d", t.Format("02"),
		"%H", t.Format("15"),
		"%M", t.Format("04"),
		"%S", t.Format("05"),
		"%%", "%",
	)
	return replacer.Replace(tmpl)
}
