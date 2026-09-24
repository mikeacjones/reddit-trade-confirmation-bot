package workflows

import (
	"go.temporal.io/sdk/workflow"
)

// listenSignal runs a workflow goroutine that receives signals forever and calls handle.
func listenSignal[T any](ctx workflow.Context, name string, handle func(T)) {
	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, name)
		for {
			var v T
			ch.Receive(ctx, &v)
			handle(v)
		}
	})
}

// listenSignalEmpty receives signals with no payload.
func listenSignalEmpty(ctx workflow.Context, name string, handle func()) {
	workflow.Go(ctx, func(ctx workflow.Context) {
		ch := workflow.GetSignalChannel(ctx, name)
		for {
			ch.Receive(ctx, nil)
			handle()
		}
	})
}
