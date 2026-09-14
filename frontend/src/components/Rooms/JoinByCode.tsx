import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { RoomsService } from "@/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

export function JoinByCode() {
  const [code, setCode] = useState("")
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: (joinCode: string) =>
      RoomsService.joinRoom({ body: { code: joinCode } }),
    onSuccess: (res) => {
      if (res.data) {
        navigate({ to: "/rooms/$roomId", params: { roomId: res.data.room_id } })
      }
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["rooms"] })
    },
  })

  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (code.trim()) mutation.mutate(code.trim())
      }}
    >
      <Input
        aria-label="Join code"
        placeholder="Join code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        className="max-w-40"
      />
      <Button type="submit" variant="outline" disabled={mutation.isPending}>
        Join
      </Button>
    </form>
  )
}
