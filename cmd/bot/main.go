package main

import (
	"fmt"
	"log/slog"
	"os"

	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/starter"
	"github.com/mikeacjones/reddit-trade-confirmation-bot/internal/worker"
)

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelInfo})))

	if len(os.Args) < 2 {
		starter.Run(nil)
		os.Exit(2)
	}

	cmd := os.Args[1]
	args := os.Args[2:]

	var err error
	switch cmd {
	case "worker":
		err = worker.Run()
	default:
		err = starter.Run(append([]string{cmd}, args...))
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}
