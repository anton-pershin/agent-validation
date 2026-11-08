(define (problem rebuild-14)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear b) (clear c) (clear e) (hand-empty) (on a f) (on b d) (on d a) (on-table c) (on-table e) (on-table f))
  (:goal (and (on c e) (on f a) (on-table a) (on-table b) (on-table d) (on-table e)))
)
